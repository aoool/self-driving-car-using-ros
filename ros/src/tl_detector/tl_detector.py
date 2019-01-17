#!/usr/bin/env python
import rospy
from std_msgs.msg import Int32
from geometry_msgs.msg import PoseStamped, Pose
from styx_msgs.msg import TrafficLightArray, TrafficLight
from styx_msgs.msg import Lane
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
from scipy.spatial import KDTree
from light_classification.tl_classifier import TLClassifier
import tf
import cv2
import yaml

STATE_COUNT_THRESHOLD = 3


class TLDetector(object):
    def __init__(self):
        rospy.init_node('tl_detector')

        self.pose_msg = None
        self.waypoints_msg = None
        self.waypoints_2d = None
        self.waypoint_tree = None
        self.camera_image_msg = None
        self.lights = []

        rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb)
        rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)

        '''
        /vehicle/traffic_lights provides you with the location of the traffic light in 3D map space and
        helps you acquire an accurate ground truth data source for the traffic light
        classifier by sending the current color state of all traffic lights in the
        simulator. When testing on the vehicle, the color state will not be available. You'll need to
        rely on the position of the light and the camera image to predict it.
        '''
        rospy.Subscriber('/vehicle/traffic_lights', TrafficLightArray, self.traffic_cb)
        rospy.Subscriber('/image_color', Image, self.image_cb)

        config_string = rospy.get_param("/traffic_light_config")
        self.config = yaml.safe_load(config_string)

        self.upcoming_red_light_pub = rospy.Publisher('/traffic_waypoint', Int32, queue_size=1)

        self.bridge = CvBridge()
        self.light_classifier = TLClassifier()
        self.listener = tf.TransformListener()

        self.state = TrafficLight.UNKNOWN
        self.last_state = TrafficLight.UNKNOWN
        self.last_wp = -1
        self.state_count = 0
        self.has_image = False

        rospy.spin()

    def pose_cb(self, msg):
        """
        Callback function for /current_pose topic subscriber.
        :param msg: /current_pose messages
        :type msg: PoseStamped
        """
        self.pose_msg = msg

    def waypoints_cb(self, msg):
        """
        Callback function for /base_waypoints topic subscriber.
        :param msg: /base_waypoints message
        :type msg: Lane
        """
        self.waypoints_msg = msg
        if self.waypoints_2d is None:
            self.waypoints_2d = [(wp.pose.pose.position.x, wp.pose.pose.position.y)
                                 for wp in self.waypoints_msg.waypoints]
            self.waypoint_tree = KDTree(self.waypoints_2d)

    def traffic_cb(self, msg):
        """
        Callback function for /vehicle/traffic_lights topic subscriber.
        :param msg: /vehicle/traffic_lights message
        :type msg: TrafficLightArray
        """
        self.lights = msg.lights

    def image_cb(self, msg):
        """
        Callback function for /image_color topic subscriber.
        Identifies red lights in the incoming camera image and publishes the index
        of the waypoint closest to the red light's stop line to /traffic_waypoint.

        :param msg: image from car-mounted camera
        :type msg: Image
        """
        self.has_image = True
        self.camera_image_msg = msg
        light_wp, state = self.process_traffic_lights()

        '''
        Publish upcoming red lights at camera frequency.
        Each predicted state has to occur `STATE_COUNT_THRESHOLD` number
        of times till we start using it. Otherwise the previous stable state is
        used.
        '''
        if self.state != state:
            self.state_count = 0
            self.state = state
        elif self.state_count >= STATE_COUNT_THRESHOLD:
            self.last_state = self.state
            light_wp = light_wp if state == TrafficLight.RED else -1
            self.last_wp = light_wp
            self.upcoming_red_light_pub.publish(Int32(light_wp))
        else:
            self.upcoming_red_light_pub.publish(Int32(self.last_wp))
        self.state_count += 1

    def get_closest_waypoint(self, x, y):
        """
        Identifies the closest path waypoint to the given position
            https://en.wikipedia.org/wiki/Closest_pair_of_points_problem
        Args:
            pose (Pose): position to match a waypoint to

        Returns:
            int: index of the closest waypoint in self.waypoints

        """
        return self.waypoint_tree.query((x, y), 1)[1]

    def get_light_state(self, light):
        """Determines the current color of the traffic light

        Args:
            light (TrafficLight): light to classify

        Returns:
            int: ID of traffic light color (specified in styx_msgs/TrafficLight)

        """
        if(not self.has_image):
            self.prev_light_loc = None
            return False

        cv_image = self.bridge.imgmsg_to_cv2(self.camera_image, "bgr8")

        #Get classification
        # takes arguments as image and classification: one of "opencv", "carla", "dl_based"
        return self.light_classifier.get_classification(cv_image, "opencv")

        # TODO Remove this once classification is available
        #return light.state

    def process_traffic_lights(self):
        """
        Finds closest visible traffic light, if one exists, and determines its location and color.

        Returns:
            int: index of waypoint closes to the upcoming stop line for a traffic light (-1 if none exists)
            int: ID of traffic light color (specified in styx_msgs/TrafficLight)
        """
        closest_light = None
        line_wp_idx = None

        # List of positions that correspond to the line to stop in front of for a given intersection.
        stop_line_positions = self.config['stop_line_positions']

        if not (None in (self.pose_msg, self.waypoints_msg, self.waypoint_tree)):
            car_wp_idx = self.get_closest_waypoint(self.pose_msg.pose.position.x,
                                                   self.pose_msg.pose.position.y)

            diff = len(self.waypoints_msg.waypoints)
            for i, light in enumerate(self.lights):
                # Get stop line waypoint index.
                line = stop_line_positions[i]
                temp_wp_idx = self.get_closest_waypoint(line[0], line[1])
                # Find closest stop line waypoint index.
                d = temp_wp_idx - car_wp_idx
                if 0 <= d < diff:
                    diff = d
                    closest_light = light
                    line_wp_idx = temp_wp_idx

        if not (None in (closest_light, line_wp_idx)):
            state = self.get_light_state(closest_light)
            return line_wp_idx, state

        return -1, TrafficLight.UNKNOWN


if __name__ == '__main__':
    try:
        TLDetector()
    except rospy.ROSInterruptException:
        rospy.logerr('Could not start traffic node.')
