import gym
import logging
from gym import spaces
import os
from aienvs.Sumo.LDM import ldm
from aienvs.Sumo.SumoHelper import SumoHelper
from aienvs.Sumo.state_representation import *
import time
from sumolib import checkBinary
import random
from aienvs.Sumo.SumoHelper import SumoHelper
from aienvs.Environment import Env
import copy
from aienvs.Sumo.TrafficLightPhases import TrafficLightPhases
from gym.spaces import Box
import numpy as np


class SumoGymAdapter(Env):
    """
    An adapter that makes Sumo behave as a proper Gym environment.
    At top level, the actionspace and percepts are in a Dict with the
    trafficPHASES as keys.

    @param maxConnectRetries the max number of retries to connect.
        A retry is needed if the randomly chosen port
        to connect to SUMO is already in use.
    """
    _DEFAULT_PARAMETERS = {'gui':True,  # gui or not
                'scene':'four_grid',  # subdirectory in the aienvs/scenarios/Sumo directory where
                'tlphasesfile':'cross.net.xml',  # file
                'box_bottom_corner':(0, 0),  # bottom left corner of the observable frame
                'box_top_corner':(10, 10),  # top right corner of the observable frame
                'resolutionInPixelsPerMeterX': 1,  # for the observable frame
                'resolutionInPixelsPerMeterY': 1,  # for the observable frame
                'y_t': 6,  # yellow time
                'car_pr': 0.5,  # for automatic route/config generation probability that a car appears
                'car_tm': 2,  #  for automatic route/config generation when the first car appears?
                'route_starts' : [],  #  for automatic route/config generation, ask Rolf
                'route_min_segments' : 0,  #  for automatic route/config generation, ask Rolf
                'route_max_segments' : 0,  #  for automatic route/config generation, ask Rolf
                'route_ends' : [],  #  for automatic route/config generation, ask Rolf
                'generate_conf' : True,  # for automatic route/config generation
                'libsumo' : False,  # whether libsumo is used instead of traci
                'waiting_penalty' : 1,  # penalty for waiting
                'new_reward': False,  # some other type of reward ask Miguel
                'lightPositions' : {},  # specify traffic light positions
                'scaling_factor' : 1.0,  # for rescaling the reward? ask Miguel
                'maxConnectRetries':50,  # maximum reattempts to connect by Traci
                }

    def __init__(self, parameters:dict={}):
        """
        @param path where results go, like "Experiment ID"
        @param parameters the configuration parameters.
        gui: whether we show a GUI.
        scenario: the path to the scenario to use
        """
        logging.debug(parameters)
        self._parameters = copy.deepcopy(self._DEFAULT_PARAMETERS)
        self._parameters.update(parameters)

        dirname = os.path.dirname(__file__)
        tlPhasesFile = os.path.join(dirname, "../../scenarios/Sumo/", self._parameters['scene'], self._parameters['tlphasesfile'])
        self._tlphases = TrafficLightPhases(tlPhasesFile)
        self.ldm = ldm(using_libsumo=self._parameters['libsumo'])

        self._takenActions = {}
        self._yellowTimer = {}
        self._chosen_action = None
        self.seed(42)  # in case no seed is given
        self._action_space = self._getActionSpace()

    def step(self, actions:dict):
        self._set_lights(actions)
        self.ldm.step()
        obs = self._observe()
        done = self.ldm.isSimulationFinished()
        global_reward = self._computeGlobalReward()

        # if done:
        #     from pympler import asizeof
        #     print("size of taken actions:", asizeof.asizeof(self._takenActions))
        #     print("size of yellow timer:", asizeof.asizeof(self._yellowTimer))
        #     print("size of tlphases:", asizeof.asizeof(self._tlphases))
        #     print("size of parameters:", asizeof.asizeof(self._parameters))
        #     print("size of action space:", asizeof.asizeof(self._action_space))
        #     print("size of ldm:", asizeof.asizeof(self.ldm))
        #     print("size of chosen action:", asizeof.asizeof(self._chosen_action))
        #     print(self._takenActions)

        # as in openai gym, last one is the info list
        return obs, global_reward, done, []

    def reset(self):
        try:
            logging.debug("LDM closed by resetting")
            self.ldm.close()
        except:
            logging.debug("No LDM to close. Perhaps it's the first instance of training")

        logging.debug("Starting SUMO environment...")
        self._startSUMO()
        # TODO: Wouter: make state configurable ("state factory")
        self._state = LdmMatrixState(self.ldm, [self._parameters['box_bottom_corner'], self._parameters['box_top_corner']], "byCorners")

        return self._observe()

        # TODO: change the defaults to something sensible
    def render(self, delay=0.0):
        import colorama
        colorama.init()

        def move_cursor(x, y):
            print ("\x1b[{};{}H".format(y + 1, x + 1))

        def clear():
            print ("\x1b[2J")

        clear()
        move_cursor(100, 100)
        import numpy as np
        np.set_printoptions(linewidth=100)
        print(self._observe())
        time.sleep(delay)

    def seed(self, seed):
        self._seed = seed

    def close(self):
        self.__del__()

    @property
    def observation_space(self):
        size = self._state.size()
        return Box(low=0, high=np.inf, shape=(size[0], size[1]), dtype=np.int32)
        # return self._state.update_state()

    @property
    def action_space(self):
        return self._action_space

    ########## Private functions ##########################
    def __del__(self):
        logging.debug("LDM closed by destructor")
        if 'ldm' in locals():
            self.ldm.close()

    def _startSUMO(self):
        """
        Start the connection with SUMO as a subprocess and initialize
        the traci port, generate route file.
        """
        val = 'sumo-gui' if self._parameters['gui'] else 'sumo'
        maxRetries = self._parameters['maxConnectRetries']
        sumo_binary = checkBinary(val)

        # Try repeatedly to connect
        while True:
            try:
                # this cannot be seeded
                self._port = random.SystemRandom().choice(list(range(10000, 20000)))
                self._sumo_helper = SumoHelper(self._parameters, self._port, self._seed)
                conf_file = self._sumo_helper.sumocfg_file
                logging.debug("Configuration: " + str(conf_file))
                sumoCmd = [sumo_binary, "-c", conf_file, "-W", "-v", "false", "--seed", str(self._seed)] # shut up SUMO
                self.ldm.start(sumoCmd, self._port)
            except Exception as e:
                if str(e) == "connection closed by SUMO" and maxRetries > 0:
                    maxRetries = maxRetries - 1
                    continue
                else:
                    raise
            else:
                break

        self.ldm.init(waitingPenalty=self._parameters['waiting_penalty'], new_reward=self._parameters['new_reward'])  # ignore reward for now
        self.ldm.setResolutionInPixelsPerMeter(self._parameters['resolutionInPixelsPerMeterX'], self._parameters['resolutionInPixelsPerMeterY'])
        self.ldm.setPositionOfTrafficLights(self._parameters['lightPositions'])

        if list(self.ldm.getTrafficLights()) != self._tlphases.getIntersectionIds():
            raise Exception("environment traffic lights do not match those in the tlphasesfile "
                    +self._parameters['tlphasesfile'] + str(self.ldm.getTrafficLights())
                    +str(self._tlphases.getIntersectionIds()))

    def _intToPhaseString(self, intersectionId:str, lightPhaseId: int):
        """
        @param intersectionid the intersection(light) id
        @param lightvalue the PHASES value
        @return the intersection PHASES string eg 'rrGr' or 'GGrG'
        """
        logging.debug("lightPhaseId" + str(lightPhaseId))
        return self._tlphases.getPhase(intersectionId, lightPhaseId)

    def _observe(self):
        """
        Fetches the Sumo state and converts in a proper gym observation.
        The keys of the dict are the intersection IDs (roughly, the trafficLights)
        The values are the state of the TLs
        """
        return self._state.update_state()

    def _computeGlobalReward(self):
        """
        Computes the global reward
        """
        return self._state.update_reward() / self._parameters['scaling_factor']

    def _getActionSpace(self):
        """
        @returns the actionspace: a dict containing <id,phases> where
        id is the intersection id and value is
         all possible actions for each id as specified in tlphases
        """
        return spaces.Dict({inters:spaces.Discrete(self._tlphases.getNrPhases(inters)) \
                            for inters in self._tlphases.getIntersectionIds()})

    def _set_lights(self, actions:spaces.Dict):
        """
        Take the specified actions in the environment
        @param actions a list of
        """
        for intersectionId in actions.keys():
            action = self._intToPhaseString(intersectionId, actions.get(intersectionId))
            # Retrieve the action that was taken the previous step
            try:
                prev_action = self._takenActions[intersectionId]
            except KeyError:
                # If KeyError, this is the first time any action was taken for this intersection
                prev_action = action
                self._takenActions.update({intersectionId:action})
                self._yellowTimer.update({intersectionId:0})

            # Check if the given action is different from the previous action
            if prev_action != action:
                # Either the this is a true switch or coming grom yellow
                action, self._yellowTimer[intersectionId] = self._correct_action(prev_action, action, self._yellowTimer[intersectionId])

            # Set traffic lights
            self.ldm.setRedYellowGreenState(intersectionId, action)
            self._takenActions[intersectionId] = action

    def _correct_action(self, prev_action, action, timer):

        """
        Check what we are going to do with the given action based on the
        previous action.
        """
        # Check if the agent was in a yellow state the previous step
        if 'y' in prev_action:
            # Check if this agent is in the middle of its yellow state
            if timer > 0:
                new_action = prev_action
                timer -= 1
            # Otherwise we can get out of the yellow state
            else:
                new_action = self._chosen_action
                if not isinstance(new_action, str):
                    raise Exception("chosen action is illegal")
        # We are switching from green to red, initialize the yellow state
        else:
            self._chosen_action = action
            if self._parameters['y_t'] > 0:
                new_action = prev_action.replace('G', 'y')
                timer = self._parameters['y_t'] - 1
            else:
                new_action = action
                timer = 0

        return new_action, timer
