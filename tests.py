import matplotlib.pyplot as plt
from mission_plan import *

# Your existing code here

def generate_waypoints_for_multiple_auvs(num_agents, swath, rect_width, rect_height, speed, straight_slack=1, overlap_between_rows=0, overlap_between_lanes=0, double_sided=False, center_x=False, center_y=False, exiting_line=False):
    timed_paths_list = []
    for auv_id in range(num_agents):
        timed_paths = plan_simple_lawnmower(
            num_agents=1,  # Plan for one AUV at a time
            swath=swath,
            rect_width=rect_width,
            rect_height=rect_height,
            speed=speed,
            straight_slack=straight_slack,
            overlap_between_rows=overlap_between_rows,
            overlap_between_lanes=overlap_between_lanes,
            double_sided=double_sided,
            center_x=center_x,
            center_y=center_y,
            exiting_line=exiting_line
        )
        timed_paths_list.append(timed_paths[0])  # Extract the single AUV's path

    return timed_paths_list

if __name__ == '__main__':
    # Example configuration
    num_agents = 6
    swath = 50
    rect_width = 500
    rect_height = 1000
    speed = 1.5
    straight_slack = 1
    overlap_between_rows = 0
    overlap_between_lanes = 0
    double_sided = False
    center_x = False
    center_y = False
    exiting_line = False

    # timed_paths_list = generate_waypoints_for_multiple_auvs(
    #     num_agents=num_agents,
    #     swath=swath,
    #     rect_width=rect_width,
    #     rect_height=rect_height,
    #     speed=speed,
    #     straight_slack=straight_slack,
    #     overlap_between_rows=overlap_between_rows,
    #     overlap_between_lanes=overlap_between_lanes,
    #     double_sided=double_sided,
    #     center_x=center_x,
    #     center_y=center_y,
    #     exiting_line=exiting_line
    # )

    timed_paths_list = plan_simple_lawnmower(
        num_agents=num_agents,
        swath=swath,
        rect_width=rect_width,
        rect_height=rect_height,
        speed=speed,
        straight_slack=straight_slack,
        overlap_between_rows=overlap_between_rows,
        overlap_between_lanes=overlap_between_lanes,
        double_sided=double_sided,
        center_x=center_x,
        center_y=center_y,
        exiting_line=exiting_line
    )

    # Visualization
    fig = plt.figure()
    ax = fig.add_subplot(111, aspect='equal')

    for timed_paths in timed_paths_list:
        timed_paths.visualize(ax, wp_labels=False, circles=True, alpha=0.1, c='k')

    plt.show()
