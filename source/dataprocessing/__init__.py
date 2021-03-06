import logging
import os
import re
from itertools import chain
from pathlib import Path

import cv2
import imutils
import numpy as np
import pandas as pd

import source.funciones as funciones
from source.entities.person import Person
from source.entities.person_frames import PersonMovement
from source.funciones import read_labels_txt

FORMAT = "%(asctime)s - %(levelname)s: %(message)s"
logging.basicConfig(format=FORMAT)
logger = logging.getLogger(__name__)

formatter = logging.Formatter(FORMAT)
logger.setLevel(logging.INFO)


class DataProcessor:
    """Class used to process data to generate training examples. Has the recquired functions
    to read a video from a directory and extract the frames. Once a labels file is provided
    with the valid frames for each video, the frame groups are made and training data is generated
    and written in a file for later usage.
    
    Returns:
        DataProcessor:
    """

    def __init__(self, model_path=None, input_dim=(257, 257), threshold=0.5, rescale=(1, 1), backbone='resnet',
                 output_stride=None):
        """Constructor for the DataProcessor class.
        
        Args:
            model_path (str, optional): Path for the TFLite Posenet file. If None and by default is
            searched in the root/models folder of the repository
            input_dim (tuple, optional): Input dimension for the previously specified model. Defaults to (257, 257).
            threshold (float, optional): Confidence threshold for considering a body joint valid. Defaults to 0.5.
            rescale (tuple, optional): Rescaling factor in the output. Defaults to (1,1).
        """
        if model_path is None:
            if backbone == 'resnet':
                MODEL_PATH = Path(__file__).parents[2].joinpath('models/resnet_stride16/model-stride16.json')
            else:
                MODEL_PATH = Path(__file__).parents[2].joinpath(
                    "models/posenet_mobilenet_v1_100_257x257_multi_kpt_stripped.tflite")
        else:
            MODEL_PATH = model_path

        if backbone == 'resnet':
            assert output_stride is not None, 'A value for output_stride must be provided when using resnet as backbone'
            dimensions = (200, 256)
            self.model, graph = funciones.load_model_resnet(str(MODEL_PATH))  # Actually a session.
            self.input_details, self.output_details = funciones.get_tensors_graph(graph)
            self.prepare_frame = funciones.prepare_frame_resnet
            self.input_dim = [(int(x) // output_stride) * output_stride + 1 for x in dimensions]
            self.get_model_output = funciones.get_model_output_resnet
        else:
            self.model, self.input_details, self.output_details = funciones.load_model_mobilenet(str(MODEL_PATH))
            self.prepare_frame = funciones.prepare_frame_mobilenet
            self.input_dim = input_dim
            self.get_model_output = funciones.get_model_output_mobilenet

        self.threshold = threshold
        self.rescale = rescale
        self.output_stride = output_stride
    @staticmethod
    def process_video(filename, input_path=None, output_path=None, output_shape=(257, 257), fps_reduce=2, angle=0):
        """Process a video from the resources folder and saves all the frames
        inside a folder with the name of the video
        FILENAME_frame_X.jpg
        
        Args:
            filename (str): Name of the video inside resources
            output_shape (tuple, optional): Size of the output images. Defaults to (256,256).
            fps_reduce (int, optional): Take one image out of  #fps_reduce. 
            Defaults to 2.
            angle (int): Angle that the video images should be rotated. 
        """
        if output_path is None:
            OUTPUT_PATH = Path(__file__).parents[2].joinpath("resources/{}".format(filename))
        else:
            OUTPUT_PATH = Path(output_path).joinpath("/{}".format(filename))

        if input_path is None:
            INPUT_PATH = Path(__file__).parents[2].joinpath("resources/{}".format(filename + ".mp4"))
        else:
            INPUT_PATH = Path(output_path).joinpath("/{}".format(filename + ".mp4"))

        try:
            os.mkdir(OUTPUT_PATH)
        except:
            os.system("rm -r {}".format(OUTPUT_PATH))
            os.mkdir(OUTPUT_PATH)

        # Read video
        video = cv2.VideoCapture(str(INPUT_PATH))
        count = 0
        logger.debug("Started reading frames.")
        while video.isOpened():
            logger.debug(
                "Reading frame {}/{} from file {}".format(count + 1, video.get(cv2.CAP_PROP_FRAME_COUNT), filename))

            # Frame reading, reshaping and saving
            _, frame = video.read()
            frame = cv2.resize(frame, output_shape)
            # if DataProcessor.check_rotation("./resources/{}".format(filename)) is not None:

            frame = imutils.rotate(frame, angle)

            if count % fps_reduce == 0:
                cv2.imwrite(
                    str(OUTPUT_PATH.joinpath("{}_frame_{}.jpg".format(filename.split(".")[0], count // fps_reduce))),
                    frame)
            count = count + 1

            if cv2.waitKey(10) & 0xFF == ord('q'):
                break
            if video.get(cv2.CAP_PROP_POS_FRAMES) == video.get(cv2.CAP_PROP_FRAME_COUNT):
                # If the number of captured frames is equal to the total number of frames,
                break
        logger.debug("Stop reading files.")
        video.release()

    def training_file_writer(self, labels_path=None, output_file=None, append=False, n=5, times_v=10):
        """This function is the main function inside DataProcessor file. It runs the whole pipeline, in this order:
        - Gets actions and frame intervals from the labels file
        - Processes the frame intervals, keeping only the valid ones.
        - Groups the frames in groups of n
        - Coordinates are calculated from those groups
        - The coordinates are added to the output file in .csv format
        
        Args:
            labels_path (str, optional): Absolute path of the labels file. If none is taken from
            action-detection/resources.
            output_file (str, optional): Absolute path of the output csv file. If none is saved into 
            action-detection/resources/training_data.csv.
            append (bool, optional): If True, the calculated coordinates are ADDED to the file
            if it's not empty. Defaults to False.
            n (int, optional): Number of frames to obtain coordinates from. Defaults to 5.
            times_v (int, optional): Times point speed is introduced into coordinates. Defaults to 10.
        
        Returns:
            pandas.DataFrame: DataFrame containing the obtained coordinates and the ones in output_file
            if append = True
        """
        if labels_path is None:
            labels_path = Path(__file__).parents[2].joinpath("resources/{}".format("labels.txt"))
        else:
            labels_path = Path(labels_path)
        if output_file is None:
            output_file = Path(__file__).parents[2].joinpath("resources/{}".format("training_data.csv"))
        else:
            output_file = Path(output_file)

        # Obtain the dictionary of coordinates
        coordinates_dict = self.get_coordinates(str(labels_path), n=n, times_v=times_v)
        try:
            if append:
                df_initial = pd.read_csv(str(output_file))
                df_list = [df_initial]
            else:
                df_list = []
        except:
            if append:
                logger.warning("Append is set to true but the reading gave an exception")
            df_list = []

        for video in coordinates_dict:
            if len(coordinates_dict[video]) == 0:
                continue
            else:
                array = np.vstack(coordinates_dict[video])
                df = pd.DataFrame(array)
                action = video.split("_")[0]
                df["action"] = [action] * len(coordinates_dict[video])
                df_list.append(df)
        logger.info(df_list)
        cols_model_orig = [int(x) for x in list(df_list[-1].columns) if str(x).isnumeric()]
        cols_model_target = [str(x) for x in cols_model_orig if str(x).isnumeric()]
        mapper = {}
        for orig, target in zip(cols_model_orig, cols_model_target):
            mapper[orig] = target

        df_list = [df_iter.rename(mapper, axis='columns') for df_iter in df_list]

        logger.info("Concatenating {} DataFrames before writing.".format(len(df_list)))

        df = pd.concat(df_list, axis=0, ignore_index=True)

        df.to_csv(str(output_file), index=False, header=False)
        return df

    def get_coordinates(self, labels_path=None, n=5, times_v=10):
        """This functions is a wrapper that makes this steps:
            - Gets actions and frame intervals from the labels file
            - Processes the frame intervals, keeping only the valid ones.
            - Groups the frames in groups of n
            - Coordinates are calculated from those groups
        Args:
            labels_path (str, optional): Absolute for the labels file. If none, it is searched inside
            action-recognition/resources
            n (int, optional): Lenght of the frame list to process. Defaults to 5.
            times_v (int, optional): Times speeds of the points is introduced as coordinate. Defaults to 10.
        
        Returns:
            dict: Dictionary that contains for each video in the labels file the coordinates after running the
            frame selection pipeline.
        """

        logger.info("Calculating coordinates from labels_path {}".format(labels_path))
        if labels_path is None:
            labels_path = Path(__file__).parents[2].joinpath("resources/{}".format("labels.txt"))
        else:
            labels_path = Path(labels_path)
        actions = DataProcessor.find_actions(labels_path)
        frame_groups = self.get_frame_groups(actions, labels_path, n)
        coordinates_dict = {}
        for video in frame_groups:
            logger.debug("Calculating coordinates for video {}".format(video))
            for group in frame_groups[video]:
                if len(group) == 0:
                    continue
                else:
                    if video not in coordinates_dict:
                        coordinates_dict[video] = []
                    persons = [element[1] for element in group]
                    coordinates = PersonMovement(persons, times_v,
                                                joints_remove=(13, 14, 15, 16),
                                                model='NN').coords.flatten()

                    logger.info("Tamaño de las coordenadas: {}".format(coordinates.shape))
                    coordinates_dict[video].append(coordinates)
        return coordinates_dict

    def process_frame(self, image_path):
        """Receives a frame path and returns the person associated
        
        Args:
            image_path (str): String containig the path of an image
        
        Returns:
            Person: Person associated to that frame.
        """
        logger.debug("Processing frame {}".format(image_path.split("/")[-1]))
        frame = cv2.imread(image_path)
        frame = self.prepare_frame(frame, self.input_dim)
        output_data, offset_data = self.get_model_output(self.model, frame, self.input_details, self.output_details)
        return Person(output_data, offset_data, self.rescale, self.threshold, output_stride=self.output_stride)

    def process_live_frame(self, frame):
        """Receives a frame path and returns the person associated
        
        Args:

        
        Returns:
            Person: Person associated to that frame.
        """
        logger.debug("Processing frame passed to the function (live).")

        frame = self.prepare_frame(frame, self.input_dim)
        output_data, offset_data = self.get_model_output(self.model, frame, self.input_details, self.output_details)
        return Person(output_data, offset_data, self.rescale, self.threshold, output_stride=self.output_stride)

    def get_frame_groups(self, actions, labels_path, n=5):
        """From a labels path, a list of actions and a number of frames per
        training data row gets all posible groups of frames to process.
        
        Args:
            labels_path (str): Path to the labels.txt file
            actions (list): Actions to process
            n (int, optional): Size of the list of people needed. Defaults to 5.
        
        Returns:
            [type]: [description]
        """
        logger.info("Getting frame groups for labels in {}".format(labels_path))
        frame_groups = {}
        self.people_dict = {}
        labels = read_labels_txt(str(labels_path), actions)
        for label in labels:
            logger.debug("Getting grame groups for label {}".format(label))
            # Groups of frames longer than n
            valid_frame_intervals = [group for group in labels[label] if group[1] - group[0] >= n - 1]
            # Transform each interval in a list of valid persons
            frame_person_list = [self.get_valid_persons(label, interval, n) for interval in valid_frame_intervals]
            # Get groups of n contiguous persons
            valid_persons_groups = [self.valid_groups(lst, n) for lst in frame_person_list]
            filter_nones = [element for element in valid_persons_groups if element is not None]
            # Complete dictionary
            frame_groups[label] = filter_nones
        # There is an undesired extra level in the lists generated. We remove it
        frame_groups_definitive = {}
        logging.info("Cleaning frame groups.")
        for video in frame_groups:
            frame_groups_definitive[video] = list(chain.from_iterable(frame_groups[video]))
        return frame_groups_definitive

    def get_valid_persons(self, fle, interval, n):
        logger.debug("Getting valid persons from file {}, interval {}".format(fle, interval))
        persons_list = self.frame_interval_to_people_list(fle, interval)

        # Now we return all the persons in the interval. Valids will be filtered
        # Into consideration the position in the frame.

        return persons_list

    def frame_interval_to_people_list(self, fle, interval, images_path=None):
        """From an interval [start, end] of frames from video, returns a list
        of tuples (index, person(i_Frame)).
        
        Args:
            file (str): folder containing frames
            interval (list): start and end of the interval
        
        Returns:
            list: List of Persons calculated from images
        """
        logger.debug("Calculating people list from interval {} in file {}".format(interval, fle))
        if images_path is None:
            PATH = Path(__file__).parents[2].joinpath("resources/{}".format(fle))
        else:
            PATH = Path(images_path).joinpath("/{}".format(fle))

        return [[i, self.process_frame(str(PATH) + "/{}_frame_{}.jpg".format(fle, i))] \
                for i in range(interval[0], interval[1] + 1)]

    def valid_groups(self, lst, n):
        """Given a list of persons, returns the valid lists of contiguous persons
        (frames)
        
        Args:
            n (int): Size of the desired lists of persons
            lst (list): List of lists [int, Person]
        """
        valid, result, aux = 0, [], []
        if lst is not None:
            for index, i in enumerate(lst):
                # if it's not the first frame --> Infer keypoints
                # If is the first frame and the frame is valid
                if valid == 0 and i[1].is_valid_first():
                    # New group
                    aux.append(i)
                    valid += 1

                # If it's not the first and frames are contiguous
                elif valid > 0 and i[0] - aux[valid - 1][0] == 1:

                    # If this frame does not complete a group then append to aux
                    if valid < n - 1 and i[1].is_valid_other():
                        i[1].infer_lc_keypoints(lst[index - 1][1])
                        # Value is valid
                        aux.append(i)
                        valid += 1
                    # If this frame completes a group append the resutl
                    elif valid == n - 1 and i[1].is_valid_other():
                        i[1].infer_lc_keypoints(lst[index - 1][1])
                        # Group is now complete
                        aux.append(i)
                        result.append(aux)
                        aux = []
                        valid = 0
                    # If frames were contiguous, the frame was not valid as other, it becomes first frame if
                    # valid as first frame
                    elif i[1].is_valid_first():
                        aux = [i]
                        valid = 1
                    # If the next frame is not valid_other and neither does valid_first, we will start
                    # from scratch
                    else:
                        aux = []
                        valid = 0
                # If frames wew not contiguous and this frame is valid as first, we try that
                elif valid > 0 and i[0] - aux[valid - 1][0] != 1 and i[1].is_valid_first():
                    aux = [i]
                    valid = 1
                # In any other case, we will start from scratch
                else:
                    aux = []
                    valid = 0
            return result
        else:
            return None

    @staticmethod
    def find_actions(file):
        actions = set()
        regex = r"[a-z]+"
        for line in open(str(file)):
            for match in re.finditer(regex, line):
                actions.add(match.group())
        return list(actions)
