import rospy
import rospkg
import os
import cv2
import sys
import threading
import numpy as np
import tensorflow as tf
from styx_msgs.msg import TrafficLight
from abc import ABCMeta, abstractmethod


class TLClassifier(object):
    """
    Base class for traffic light classifiers. The subclasses should provide implementations for the following methods:
        TLClassifier._classify(self, image)
        <TLClassifier-Subclass>.__init__(self)
    Note that <TLClassifier-Subclass>.__init__(self) must invoke its parent constructor
    and should not have input arguments except self.
    """

    __metaclass__ = ABCMeta

    INSTANCE = None
    KNOWN_TRAFFIC_LIGHT_CLASSIFIERS = {}  # it is not empty; it is filled by TLClassifier.register_subclass decorator

    @classmethod
    def register_subclass(cls, cls_id):
        """
        Decorator for TLClassifier subclasses.
        Adds decorated class to the cls.KNOWN_TRAFFIC_LIGHT_CLASSIFIERS dictionary.
        :param cls_id: string identifier of the classifier
        :return: function object
        """
        def reg_subclass(cls_type):
            cls.KNOWN_TRAFFIC_LIGHT_CLASSIFIERS[cls_id] = cls_type
            return cls_type
        return reg_subclass

    @classmethod
    def get_instance_of(cls, classifier_name):
        """
        It is a factory method for the `tl_classifier` module. It returns an instance of the classifier
        based on the input argument provided.
        :param classifier_name: name of the classifier
        :type classifier_name: str
        :return: instance of the classifier corresponding to the classifier string identifier
        :rtype: TLClassifier
        """
        if cls.INSTANCE is not None \
                and type(cls.INSTANCE) != cls.KNOWN_TRAFFIC_LIGHT_CLASSIFIERS[classifier_name]:
            raise ValueError("cannot instantiate an instance of " + classifier_name
                             + " classifier since an instance of another type (" + type(cls.INSTANCE).__name__ +
                             ") has already been instantiated")

        if cls.INSTANCE is not None:
            return cls.INSTANCE

        classifier_type = cls.KNOWN_TRAFFIC_LIGHT_CLASSIFIERS.get(classifier_name, None)
        if classifier_type is None:
            raise ValueError("classifier_name parameter has unknown value: " + classifier_name
                             + "; the value should be in " + str(cls.KNOWN_TRAFFIC_LIGHT_CLASSIFIERS.keys()))
        cls.INSTANCE = classifier_type()

        return cls.INSTANCE

    @abstractmethod
    def _classify(self, image):
        """
        Determines the color of the traffic light in the image.
        This method should be implemented by a particular type of the traffic light classifier.

        :param image: image containing the traffic light
        :type image: np.ndarray
        :returns: ID of traffic light color (specified in styx_msgs/TrafficLight)
        :rtype: int
        """
        raise NotImplementedError()

    def classify(self, image):
        """
        Determines the color of the traffic light in the image.
        Prints FPS statistic approximately each second.

        :param image: image containing the traffic light
        :type image: np.ndarray
        :returns: ID of traffic light color (specified in styx_msgs/TrafficLight)
        :rtype: int
        """
        # calculate FPS based on the number of images processed per second;
        # ensure that self._counter value does not go over integer limits
        with self._lock:
            if self._start_time is None or self._counter > (sys.maxint - 100):
                self._start_time = rospy.get_time()
                self._counter = 0
            self._counter += 1
            # save start time and counter values for processing outside of the critical section
            start_t = self._start_time
            counter = self._counter

        tl_state = self._classify(image)

        # log the FPS no faster than once per second
        diff_t =  rospy.get_time() - start_t
        fps = None
        if diff_t >= 1.0:
            fps = int(counter / diff_t)
        if fps is not None:  # do not log while there are only a few images processed
            rospy.logdebug_throttle(1.0, "FPS: %d" % fps)

        return tl_state

    @abstractmethod
    def get_state_count_threshold(self, last_state):
        """
        Returns state count threshold value based on the last state.
        :param last_state: last traffic lights state
        :return: threshold value
        :rtype: int
        """
        raise NotImplementedError()

    @abstractmethod
    def __init__(self, cls_name):
        """
        Constructor is marked as @abstractmethod to force implemnting the __init__ method in subclasses.
        Subclasses must invoke their parent constructors.
        :param cls_name: string identifier of the subclass.
        """
        rospy.loginfo("instantiating %s (available classifiers: %s)",
                      cls_name, str(self.KNOWN_TRAFFIC_LIGHT_CLASSIFIERS.keys()))

        self._lock = threading.Lock()  # lock to be used in TLClassifier.classify to increment invocation counter
        self._counter = 0
        self._start_time = None


@TLClassifier.register_subclass('opencv')
class OpenCVTrafficLightsClassifier(TLClassifier):
    """
    Detects and classifies traffic lights on images with Computer Vision techniques.
    """

    def get_state_count_threshold(self, state):
        return 3

    def _classify(self, image):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        lower_red1 = np.array([0, 100, 100])
        upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([160, 100, 100])
        upper_red2 = np.array([179, 255, 255])

        mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
        mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
        red_img = cv2.addWeighted(mask1, 1.0, mask2, 1.0, 0)

        im, contours, hierarchy = cv2.findContours(red_img, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        red_count = 0
        for x, contour in enumerate(contours):
            contourarea = cv2.contourArea(contour)  # get area of contour
            if 18 < contourarea < 900:  # Discard contours with a too large area as this may just be noise
                arclength = cv2.arcLength(contour, True)
                approxcontour = cv2.approxPolyDP(contour, 0.01 * arclength, True)
                # Check for Square
                if len(approxcontour) > 5:
                    red_count += 1
        rospy.logdebug("Red count: %d", red_count)
        if red_count > 0:
            return TrafficLight.RED

        return TrafficLight.UNKNOWN

    def __init__(self):
        super(OpenCVTrafficLightsClassifier, self).__init__(self.__class__.__name__)


@TLClassifier.register_subclass("ssd")
class SSDTrafficLightsClassifier(TLClassifier):

    def get_state_count_threshold(self, last_state):
        if last_state == TrafficLight.RED:
            # High threshold for accelerating
            return 3
        else:
            # Low threshold for stopping
            return 1

    @staticmethod
    def load_graph(graph_file):
        """Loads a frozen inference graph"""
        graph = tf.Graph()
        with graph.as_default():
            od_graph_def = tf.GraphDef()
            with tf.gfile.GFile(graph_file, 'rb') as fid:
                serialized_graph = fid.read()
                od_graph_def.ParseFromString(serialized_graph)
                tf.import_graph_def(od_graph_def, name='')

        return graph

    def _classify(self, image):
        image_np = np.expand_dims(np.asarray(image, dtype=np.uint8), 0)
        # Actual detection
        (boxes, scores, classes) = self.sess.run([self.detection_boxes, self.detection_scores, self.detection_classes],
                                                 feed_dict={self.image_tensor: image_np})

        # Remove unnecessary dimensions
        boxes = np.squeeze(boxes)
        scores = np.squeeze(scores)
        classes = np.squeeze(classes)

        for i in range(len(classes)):
            rospy.logdebug('class = %s, score = %s', self.labels_dict[classes[i]], str(scores[i]))
            # if red or yellow light with confidence more than 10%
            if (classes[i] == 2 or classes[i] == 3) and scores[i] > 0.1:
                return TrafficLight.RED

        return TrafficLight.UNKNOWN

    def __init__(self):
        super(SSDTrafficLightsClassifier, self).__init__(self.__class__.__name__)

        # Model path
        package_root_path = rospkg.RosPack().get_path('tl_detector')
        model_path = os.path.join(package_root_path, 'models/ssd.pb')

        # Labels dictionary
        self.labels_dict = {1: 'Green', 2: 'Red', 3: 'Yellow', 4: 'Unknown'}

        # Load frozen graph of trained model
        self.detection_graph = self.load_graph(model_path)

        # Get tensors
        self.image_tensor = self.detection_graph.get_tensor_by_name('image_tensor:0')
        self.detection_boxes = self.detection_graph.get_tensor_by_name('detection_boxes:0')
        self.detection_scores = self.detection_graph.get_tensor_by_name('detection_scores:0')
        self.detection_classes = self.detection_graph.get_tensor_by_name('detection_classes:0')

        # Create session
        self.sess = tf.Session(graph=self.detection_graph)
