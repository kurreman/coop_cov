#! /usr/bin/env python
# -*- coding: utf-8 -*-
# vim:fenc=utf-8
# Ozer Ozkahraman (ozero@kth.se)

import matplotlib.pyplot as plt
plt.rcParams['pdf.fonttype'] = 42
import numpy as np
import dubins
import time
from descartes import PolygonPatch
from shapely.ops import unary_union
from shapely.geometry import Polygon, Point
import matplotlib.patches as pltpatches

from toolbox import geometry as geom
from auv import AUV
from mission_plan import TimedWaypoint, MissionPlan
from pose_graph import PoseGraph, PGO_VertexIdStore


class Agent(object):
    COLORS = ['red', 'blue', 'green', 'purple', 'orange', 'cyan']
    def __init__(self,
                 real_auv,
                 pose_graph,
                 mission_plan,
                 drift_model = None):

        # a reference to the actual physical auv
        # for ceonvenience
        self._real_auv = real_auv

        # if this agents auv doesnt move, then this agent is just a landmark
        # to be observed by other agents
        # use this flag to save some compute
        self.is_landmark = self._real_auv.forward_speed == 0


        self.pg = pose_graph
        self.mission_plan = mission_plan
        self.drift_model = drift_model

        # this auv model will be used to create the pose graph from
        # noisy measurements of the real auv
        self.internal_auv = AUV(auv_id = real_auv.auv_id,
                                init_pos = real_auv.pose[:2],
                                init_heading = real_auv.pose[2],
                                target_threshold = real_auv.target_threshold,
                                forward_speed = real_auv.forward_speed,
                                auv_length = real_auv.auv_length,
                                max_turn_angle = real_auv.max_turn_angle)

        self.id = real_auv.auv_id

        # keep track of time passed for waiting purposes
        self.time = 0

        # to keep track of when we were connected to another auv
        # so that we can optimize the PG when we disconnect
        self.connection_trace = []

        # keep a record of how many vertices and edges we received through "fill_in_since_last_interaction"
        # clock, num list
        self.received_data = {'verts':[(0.,0.)],
                              'edges':[(0.,0.)]}

        # list of distances between real auv and internal auv (time,err)
        self.real_errors = []
        # list of distances the real auv moved
        self.real_moved_dists = []
        # keep track of how much our error drops after optimizing (time,drop)
        self.position_error_drops = []
        # list of current_time - wp.time
        self.waypoint_reaching_times = []

        # for each waypoint in the missionplan, we might have many pts in between
        # generated by a dubins path planner
        # we need to give these one by one to the auv
        # and re-calculate the path plan when we reach a mission wp
        self.current_dubins_points = []

        # some visualization data
        self.viz_plan_points = []
        self.viz_optim_points = []
        self.viz_waited_points = []
        self.color = Agent.COLORS[self.id%len(Agent.COLORS)]


        # if this is a landmark, just add ONE odom pose
        # in the beginning and dont do anything else
        # ever again
        if self.is_landmark:
            self.pg.append_odom_pose(self._real_auv.apose)
            # self.log("Landmark pose appended")


    def log(self, *args):
        if len(args) == 1:
            args = args[0]
        print(f'[AGNT:{self.pg.pg_id}]\t{args}')


    def update(self, dt, all_agents, landmarks=None):
        # update internal auv
        # apply the same control to real auv, with enviromental noise
        # measure real auv (heading?), apply onto internal auv
        # update pose graph with internal auv

        self.time += dt

        # if this is a landmark, dont to anything, completely passive
        # update shouldnt even be called on this, but just in case
        if self.is_landmark:
            return



        ### MISSION SYNC
        at_target = False
        current_timed_wp = self.mission_plan.get_current_wp(self.id)
        if current_timed_wp is None:
            self.mission_plan.visit_current_wp(self.id)
            current_timed_wp = self.mission_plan.get_current_wp(self.id)
            if current_timed_wp is None:
                # this agent is 'done', the mission plan is out of WPs
                # do nothing
                return
            dist = geom.euclid_distance(self.internal_auv.pose[:2], current_timed_wp.pose[:2])
        else:
            dist = geom.euclid_distance(self.internal_auv.pose[:2], current_timed_wp.pose[:2])
            at_target = dist <= self.internal_auv.target_threshold
            rendezvous_happened = current_timed_wp.rendezvous_happened and\
                    current_timed_wp.idx_in_pattern in [1,3,5]
            # either at the target, or we can skip the rest of the line because
            # we basically "met in the middle" with someone else
            if at_target:
                self.waypoint_reaching_times.append((self.time, self.time - current_timed_wp.time))
                wp_time_reached = self.time >= current_timed_wp.time
                # only skip waiting at purely rendezvous WPs, wps 2 and 4 are for lining up
                if wp_time_reached or rendezvous_happened:
                    # we have reached the point, and we dont need to wait here
                    # get the next wp
                    self.mission_plan.visit_current_wp(self.id)
                    current_timed_wp = self.mission_plan.get_current_wp(self.id)
                    self.current_dubins_points = []
                else:
                    # dont move if dont have to
                    self.viz_waited_points.append(self.internal_auv.pose)

        if current_timed_wp is None:
            # this agent is 'done'
            # do nothing
            return

        ### PATH PLANNING
        # if the point is far away enough, use dubins. If it is close by, just use
        # simple heading controller of the AUV itself
        if dist < self.internal_auv.target_threshold + 0.5:
            target_posi = current_timed_wp.pose[:2]
        else:
            # first check if we already have a dubins path planned for this WP
            if self.current_dubins_points is None or len(self.current_dubins_points)==0:
                # there is no path planned for this WP, plan it now
                dubins_path = dubins.shortest_path(self.internal_auv.pose,
                                                   current_timed_wp.pose,
                                                   self.mission_plan.config['turning_rad'])
                # sample it and set the path
                pts, times = dubins_path.sample_many(0.5)
                self.current_dubins_points = pts
                self.viz_plan_points.append(self.internal_auv.pose)

            # we have a path to follow
            # skip the points that are too close
            target_posi = self.current_dubins_points[0][:2]
            while geom.euclid_distance(self.internal_auv.pose[:2], self.current_dubins_points[0][:2]) <= self.internal_auv.target_threshold:
                if len(self.current_dubins_points) > 1:
                    self.current_dubins_points = self.current_dubins_points[1:]
                    # then set the first point of the plan as the auvs target
                    target_posi = self.current_dubins_points[0][:2]
                else:
                    break



        ### MOTION
        # if this is a 'first' waypoint, stop covering
        # if its a 'last', start covering
        cover = current_timed_wp.position_in_line == TimedWaypoint.LAST

        # if we are alone
        # and there are no landmarks in the vicinity
        # we will drift
        if landmarks is None:
            landmarks = []
        alone = True
        no_landmarks = True
        for agent in all_agents+landmarks:
            # skip self
            if agent.pg.pg_id == self.pg.pg_id:
                continue
            dist = geom.euclid_distance(self._real_auv.pose[:2], agent._real_auv.pose[:2])

            lim = self.mission_plan.config['comm_range']
            if agent.is_landmark:
                lim = self.mission_plan.config['landmark_range']

            if dist <= lim:
                if agent.is_landmark:
                    no_landmarks = False
                else:
                    alone = False



        self.internal_auv.set_target(target_posi, cover=cover)
        # control real auv with what the internal one thinks
        # without any drifting knowledge
        td, ta = self.internal_auv.update(dt)

        # if we are doing coverage work, then we also drift
        moved_dist = self.internal_auv.last_moved_distance
        if cover and alone and self.drift_model is not None:
            _,_, drift_trans_angle = self.drift_model.sample(self._real_auv.pose[0],
                                                             self._real_auv.pose[1])
            drift_trans_k = self.mission_plan.config['uncertainty_accumulation_rate_k']

            # if doing coverage, use the given drift model
            # to determine the drifting distance in meters
            # k is in meters per meter. last moved distance is meters, thus drift mag is in meters
            drift_mag = moved_dist * drift_trans_k
            drift_x = drift_mag * np.cos(drift_trans_angle)
            drift_y = drift_mag * np.sin(drift_trans_angle)

            # add some heading drifting too
            degree_lim = self.mission_plan.config['heading_noise_degrees']
            heading_noise = np.deg2rad((np.random.random()*2 -1)*degree_lim)
        else:
            drift_x = 0
            drift_y = 0
            heading_noise = 0



        # and then finally update the real auv with the desired
        # motion from the internal auv and the drift that would cause
        self._real_auv.update(dt,
                              turn_direction = td,
                              turn_amount = ta,
                              drift_x = drift_x,
                              drift_y = drift_y,
                              drift_heading = 0.,
                              cover = cover)


        # compass is not _perfect_, but assume so when theres another auv nearby
        self.internal_auv.set_heading(self._real_auv.heading + heading_noise)

        # if within a landmark, we know where we are well
        if not no_landmarks:
            self.internal_auv.set_pose(self._real_auv.apose)

        # finally update the pose graph with the internal auv
        self.pg.append_odom_pose(self.internal_auv.apose)

        # keep track of errors over the whole thing
        real_err = geom.euclid_distance(self._real_auv.pose[:2], self.internal_auv.pose[:2])
        self.real_errors.append((self.time, real_err))
        self.real_moved_dists.append((self.time, moved_dist))



    def communicate(self,
                    all_agents,
                    summarize_pg=True):

        recorded = False
        comm_dist = self.mission_plan.config['comm_range']
        lm_dist = self.mission_plan.config['landmark_range']

        # quick exit if we are not planned to communicate at all
        if comm_dist > 0:
            for agent in all_agents:
                # skip self
                if agent.id == self.id:
                    continue

                lim = comm_dist
                if agent.is_landmark:
                    lim = lm_dist

                dist = geom.euclid_distance(self._real_auv.pose[:2], agent._real_auv.pose[:2])
                if dist <= lim:
                    self.pg.measure_tip_to_tip(self_real_pose = self._real_auv.pose,
                                               other_real_pose = agent._real_auv.pose,
                                               other_pg = agent.pg,
                                               landmark = agent.is_landmark)

                    use_summary = summarize_pg and not agent.is_landmark
                    num_vs, num_es = self.pg.fill_in_since_last_interaction(agent.pg, use_summary=use_summary)
                    # if num_vs > 2:
                        # self.log(f"Got {num_vs} verts from {agent.id}")
                    self.received_data['verts'].append((self.time, num_vs))
                    self.received_data['edges'].append((self.time, num_es))

                    # was not connected, just connected
                    if not recorded:
                        self.connection_trace.append(True)
                        recorded = True

        # is not connected to anyone
        if not recorded:
            self.connection_trace.append(False)
        else:
            # connected to someone
            # mark this in the waypoint we are going to, if any
            # but only if we are close enough to the wp, to avoid marking
            # it as done due to a random other rendezvous not intended for this wp
            current_timed_wp = self.mission_plan.get_current_wp(self.id)
            if current_timed_wp is not None:
                dist = geom.euclid_distance(self.internal_auv.pose[:2], current_timed_wp.pose[:2])
                if dist <= current_timed_wp.uncertainty_radius:
                    current_timed_wp.rendezvous_happened = True


        # if the connection status has changed, optimize the pose graph etc.
        if len(self.connection_trace) > 2:
            if self.connection_trace[-1] != self.connection_trace[-2]:
                success, corrected_current_pose = self.pg.optimize(use_summary=summarize_pg, save_before=False)
                if success:
                    correction_vec = self.internal_auv.apose[:2] - corrected_current_pose[:2]
                    # self.log(f"Pose correction={correction_vec}")

                    self.internal_auv.set_pose(corrected_current_pose)

                    self.viz_optim_points.append(self.internal_auv.pose)
                    # we should re-plan next update with the correcter est.
                    self.current_dubins_points = []



    def distance_traveled_error(self, just_error = False):
        # from the GT auv, find distance traveled
        if self.time < 10:
            return 0

        final_error = geom.euclid_distance(self._real_auv.apose, self.internal_auv.apose)
        if just_error:
            return final_error

        travel = self._real_auv.total_distance_traveled
        error = final_error / travel
        return error


    def visualize(self, ax):
        if self.is_landmark:
            # if this is a landmark agent, just plot a thick X for its pos
            x,y = self._real_auv.pos
            ax.scatter(x,y, alpha=1.0, c='k')
            ax.add_patch(pltpatches.Circle((x,y),
                                           radius = self.mission_plan.config['landmark_range'],
                                           ec = 'k',
                                           fc = 'k',
                                           alpha=0.2,
                                           hatch='x',
                                           fill=False))
            return

        real_trace = self._real_auv.pose_trace
        if len(real_trace) > 0:
            ax.plot(real_trace[:,0], real_trace[:,1], alpha=0.8, c=self.color)

        internal_trace = self.internal_auv.pose_trace
        if len(internal_trace) > 0:
            ax.plot(internal_trace[:,0], internal_trace[:,1], alpha=0.5, linestyle='--',  c=self.color)


        coverage_polies = self._real_auv.coverage_polygon(swath = self.mission_plan.config['swath'],
                                                          shapely=True)
        for poly in coverage_polies:
            ax.add_artist(PolygonPatch(poly, alpha=0.08, fc=self.color, ec=self.color))

        viz_lists = [
            self.viz_plan_points,
            self.viz_optim_points
        ]
        o = 1
        xoffs = [o,  o, -o, -o]
        yoffs = [o, -o, -o,  o]
        chars = ['p', 'o']
        for pts, xoff, yoff, char in zip(viz_lists, xoffs, yoffs, chars):
            if len(pts) > 0:
                for x,y,h in pts:
                    ax.text(x+xoff, y+yoff, char, color=self.color)
                    ax.scatter(x,y,c=self.color, marker='x', alpha=0.5)

        if len(self.viz_waited_points) > 0:
            p = np.array(self.viz_waited_points)
            ax.scatter(p[:,0], p[:,1], alpha=0.5, c=self.color, marker='o')



class RunnableMission:
    def __init__(
        self,
        dt,
        seed,
        mplan,
        drift_model,
        use_summary = True
    ):
        np.random.seed(seed)
        self.pg_id_store = PGO_VertexIdStore()

        self.seed = seed
        self.dt = dt
        self.mplan = mplan
        self.drift_model = drift_model
        self.agents = []
        # landmarks are agents that dont do ANYTHING
        # but simply exist for other agents to measure
        self.landmarks = []

        # coverage polygons. might have holes.
        self.covered_poly = []
        self.missed_poly = []
        # the length and width of the minimum bounding box of each
        # hole in the missed poly
        self.missed_lenwidths = []

        # (times, errors) lists for each agent
        self.all_translational_errs = []
        # (times, error_drops) lists for each agent
        self.all_error_drops = []

        # enable or disable pose graph summarization for the mission
        self.use_summary = use_summary

        for i, timed_path in enumerate(mplan.timed_paths):
            init_heading = timed_path.wps[0].pose[2]
            init_heading_vec = np.array([np.cos(init_heading), np.sin(init_heading)])
            # start _juuuust_ a little behind to cover the very very beginning
            init_pos = timed_path.wps[0].pose[:2] - init_heading_vec*0.5
            auv = AUV(auv_id = i,
                      init_pos = init_pos,
                      init_heading = np.rad2deg(init_heading),
                      target_threshold = 2,
                      forward_speed = mplan.config['speed'])

            pg = PoseGraph(pg_id = i,
                           id_store = self.pg_id_store)

            agent = Agent(real_auv = auv,
                          pose_graph = pg,
                          mission_plan = mplan,
                          drift_model = drift_model)

            self.agents.append(agent)


    def add_landmarks(self, positions):
        for i,pos in enumerate(positions):
            # negative ids for landmark agents
            ii = -i-1
            # a landmark is literally an agent that never moves
            # or does ANYTHING
            auv = AUV(auv_id = ii,
                      init_pos = pos,
                      init_heading= 0,
                      target_threshold=2,
                      forward_speed=0)

            pg = PoseGraph(pg_id = ii,
                           id_store= self.pg_id_store)

            agent = Agent(real_auv = auv,
                          pose_graph = pg,
                          mission_plan = self.mplan,
                          drift_model = None)

            self.landmarks.append(agent)




    def log(self, *args):
        if len(args) == 1:
            args = args[0]
        print(f'[M:{self.seed}]\t{args}')


    def run(self):
        step = 0
        agents = self.agents
        landmarks = self.landmarks
        dt = self.dt
        mplan = self.mplan

        prev_print_time = 0
        start_time = time.time()

        # run the agents
        while True:
            step += 1
            for agent in agents:
                agent.update(dt = dt,
                             all_agents = agents,
                             landmarks = landmarks)

            for agent in agents:
                agent.communicate(all_agents = agents+landmarks,
                                  summarize_pg = self.use_summary)

            if mplan.is_complete:
                self.log("Plan completed")
                break

            if step*dt >= mplan.last_planned_time:
                self.log("Max planned time reached")
                break

            elapsed = time.time() - prev_print_time
            if elapsed > 5:
                sim_time = step*self.dt
                elapsed = time.time() - start_time
                if elapsed == 0:
                    rate = '-'
                    estimate = '-'
                else:
                    rate = sim_time / elapsed
                    remaining = mplan.last_planned_time - sim_time
                    estimate = remaining / rate

                self.log(f"sim time={int(sim_time)}/{int(mplan.last_planned_time)}s "+
                         f"real time={int(elapsed)}s "+
                         f"rate={int(rate)}s/s "+
                         f"estimated remaining={int(estimate)}s")
                prev_print_time = time.time()

        self.calculate_stats()
        self.log("Run complete")


    def calculate_stats(self):
        self.log("Doing stats")
        # and then calculate stats
        all_polies = []
        for agent in self.agents:
            coverage_polies = agent._real_auv.coverage_polygon(swath = self.mplan.config['swath']+1,
                                                               shapely = True,
                                                               beam_radius = 1.5)
            all_polies += coverage_polies

            times, errs = zip(*agent.real_errors)
            times, dists = zip(*agent.real_moved_dists)
            distances_traveled = np.cumsum(dists)
            errs = errs / (distances_traveled+0.000001)
            self.all_translational_errs.append((times, errs))
            if len(agent.position_error_drops) > 0:
                times, drops = zip(*agent.position_error_drops)
                self.all_error_drops.append((times, drops))

        w, h = self.mplan.config['rect_width'], self.mplan.config['rect_height']
        area_poly = Polygon(shell=[
            (0,0),
            (w,0),
            (w,h),
            (0,h),
            (0,0)
        ])
        self.covered_poly = unary_union(all_polies)
        self.missed_poly = area_poly - self.covered_poly


        def get_lenwidth(poly):
            area = poly.area
            rect = poly.minimum_rotated_rectangle
            x,y = rect.exterior.coords.xy
            len01 = Point(x[0], y[0]).distance(Point(x[1], y[1]))
            len12 = Point(x[1], y[1]).distance(Point(x[2], y[2]))
            edge_lens = (len01, len12)
            rect_len = max(edge_lens)
            rect_width = min(edge_lens)
            return (rect_len, rect_width)


        if self.missed_poly.area > 0:
            try:
                hole_polies = list(self.missed_poly.geoms)
                for poly in hole_polies:
                    self.missed_lenwidths.append(get_lenwidth(poly))
            except:
                self.missed_lenwidths.append(get_lenwidth(self.missed_poly))

        total_travel = sum([agent._real_auv.total_distance_traveled for agent in self.agents])
        total_time = len(self.agents) * self.mplan.last_planned_time
        final_errors = [float(agent.real_errors[-1][1]) for agent in self.agents]

        self.results = {
            'missed_area':self.missed_poly.area,
            'total_covered_area':self.covered_poly.area,
            'intended_covered_area': area_poly.area - self.missed_poly.area,
            'intended_area':area_poly.area,
            'plan_is_complete':self.mplan.complete_plan,
            'missed_lenwidths':[list(mlw) for mlw in self.missed_lenwidths],
            'total_travel':float(total_travel),
            'total_agent_time':float(total_time),
            'final_translational_errors':final_errors
        }





    def visualize(self, ax):
        self.drift_model.visualize(ax, 10, rect=self.mplan.bounding_rectangle, alpha=0.2)
        self.mplan.visualize(ax, alpha=0.2)
        for agent in self.agents:
            agent.visualize(ax)

        for lm in self.landmarks:
            lm.visualize(ax)

        if self.missed_poly.area > 0:
            ax.add_artist(PolygonPatch(self.missed_poly, alpha=1, fc='red', ec='black'))


    def plot_errors(self):
        plt.figure()
        for agent, (times, errs) in zip(self.agents, self.all_translational_errs):
            plt.scatter(times, errs, c=agent.color, alpha=0.5)
        plt.title("Translational errors over time")
        plt.xlabel("Time $[s]$")
        plt.ylabel("Error $[m/m]$")

    def plot_err_drops(self):
        plt.figure()
        for agent, (times, drops) in zip(self.agents, self.all_error_drops):
            plt.scatter(times, drops, c=agent.color, alpha=0.5)
        plt.title("Error drops")
        plt.xlabel("Time $[s]$")
        plt.ylabel("Error drop $[m]$")


    def plot_missed_lenwidths(self, ax=None):
        a = np.array(self.missed_lenwidths)
        if len(a) == 0:
            self.log("No missed area!")
            return

        if ax is None:
            plt.figure()
            plt.scatter(a[:,0], a[:,1])
            plt.title("Length and width of missed areas")
            plt.xlabel("Length $[m]$")
            plt.ylabel("Width $[m]$")
        else:
            mission_markers = {MissionPlan.PLAN_TYPE_DUBINS:'x',
                               MissionPlan.PLAN_TYPE_SIMPLE:'o'}
            comm_colors = {True:'red',
                           False:'blue'}

            ax.scatter(
                a[:,0],
                a[:,1],
                marker=mission_markers[self.mplan.config['plan_type']],
                c=comm_colors[self.mplan.config['comm_range']>0])




if __name__ == '__main__':
    plt.ion()
    from drift_model import DriftModel
    import sys
    import time

    try:
        seed = int(sys.argv[1])
        print(f'Given seed={seed}')
    except:
        seed = int(time.time())
        print(f'Time seed={seed}')


    mplan = MissionPlan(
        plan_type = MissionPlan.PLAN_TYPE_DUBINS,
        # plan_type = MissionPlan.PLAN_TYPE_SIMPLE,
        num_agents = 2,
        swath = 25,
        rect_width = 100,
        rect_height = 100,
        speed = 1.5,
        uncertainty_accumulation_rate_k = 0.1,
        kept_uncertainty_ratio_after_loop = 0.5,
        heading_noise_degrees = 10,
        turning_rad = 5,
        comm_range = 25,
        landmark_range = 10,
        overlap_between_lanes = 5,
        overlap_between_rows = 5
    )

    drift_model = DriftModel(
        num_spirals = 10,
        num_ripples = 0,
        area_xsize = mplan.config['rect_width'],
        area_ysize = mplan.config['rect_height'],
        xbias = 0,
        ybias = 0,
        scale_size = 1,
        seed = seed
    )


    mission = RunnableMission(
        dt = 0.05,
        seed = seed,
        mplan = mplan,
        drift_model = drift_model,
        use_summary = True
    )


    mission.add_landmarks([[80,60], [20,60]])

    mission.run()

    fig = plt.figure()
    ax = fig.add_subplot(111, aspect='equal')
    mission.visualize(ax)

    # a = mission.agents[0]
    # pgo = a.pg.make_pgo()
    # pgo.optimize()
    # pgo.visualize(ax, color='purple', alpha=0.3)

    # a = mission.agents[1]
    # pgo = a.pg.make_pgo()
    # pgo.optimize()
    # pgo.visualize(ax, color='cyan', alpha=0.3)


    # fig = plt.figure()
    # ax = fig.add_subplot(111, aspect='equal')
    # mission.plot_missed_lenwidths(ax)










