import gym
import logging
from gym import spaces
from aienvs.FactoryFloor.FactoryFloorRobot import FactoryFloorRobot
from aienvs.FactoryFloor.FactoryFloorTask import FactoryFloorTask
from aienvs.FactoryFloor.FactoryFloorState import FactoryFloorState
from aienvs.Environment import Env
import numpy as np
from numpy import array, dstack, ndarray
import copy
from random import Random
from aienvs.FactoryFloor.Map import Map
from numpy.random import seed as npseed
from numpy.random import choice as weightedchoice
import time
import random
from aiagents.FixedActionsSpace import FixedActionsSpace
from aienvs.listener.Listener import Listener
import traceback
from aienvs.listener.DefaultListenable import DefaultListenable

import pdb

USE_PossibleActionsSpace = False


class FactoryFloor(Env):
    """
    The factory floor environment. This adds all dynamic aspects of the Map:
    the robots and the tasks.
    """
    DEFAULT_PARAMETERS = {'steps':1000,
                'robots':[ [3, 4], 'random'],  # initial robot positions
                'tasks': [ [1, 1] ],  # initial task positions
                # P(ACT will succeed)
                'P_action_succeed':{'LEFT':0.9, 'RIGHT':0.9, 'ACT':0.5, 'UP':0.9, 'DOWN':0.9},
                'P_task_appears':0.99,  # P(new task appears in step) 
                'allow_robot_overlap':False,
                'allow_task_overlap':False,
                'seed':None,
                'map':['..........',
                       '...8......',
                       '..3.*.....',
                       '....*.5...',
                       '...99999..']
                }

    ACTIONS = {
        0: "ACT",
        1: "UP",
        2: "DOWN",
        3: "LEFT",
        4: "RIGHT"
    }   

    def __init__(self, parameters:dict={}):
        """
        TBA
        """
        self._parameters = copy.deepcopy(self.DEFAULT_PARAMETERS)
        self._parameters.update(parameters)
        self._random = Random(x=self._parameters['seed'])
        npseed(self._parameters['seed'])
        self._map = Map(self._parameters['map'], self._parameters['P_task_appears'], self._random)
        # use "set" to get rid of weird wrappers
        # if set(self._parameters['P_action_succeed'].keys()) != set(FactoryFloor.ACTIONS.values()):
        #    raise ValueError("P_action_succeed must contain values for all actions")

        robots = []
        tasks = []
        self._state = FactoryFloorState(robots, tasks)
        for pos in self._parameters['robots']:
            if isinstance(pos, list):
                if len(pos) != 2:
                    raise ValueError("position vector must be length 2 but got " + str(pos))
                robot = FactoryFloorRobot(array(pos))
            elif pos == 'random':
                robot = FactoryFloorRobot(self._getFreeMapPosition())
            else:
                raise ValueError("Unknown robot position, expected list but got " + str(type(pos)))
            self._state.addRobot(robot)
        for pos in self._parameters['tasks']:
            self._state.addTask(FactoryFloorTask(array(pos)))
    
        if not USE_PossibleActionsSpace:
            self._actSpace = spaces.Dict({robot.getId():spaces.Discrete(len(self.ACTIONS)) for robot in self._state.robots})
            seed=self._random.randint(0,10*len(self._state.robots))
            self._actSpace.seed(seed)

    def step(self, actions:spaces.Dict):
        if(actions):
            for robot in self._state.robots:
                self._applyAction(robot, actions[robot.getId()])

        if self._random.random() < self._map.getTaskProbability():
            self._addTask()

        global_reward = -self._computePenalty()
        done = (self._parameters['steps'] <= self._state.step)
        self._state.step += 1

        obs = copy.deepcopy(self._state)
        return obs, global_reward, done, []
    
    def reset(self):
        self._state.step = 0
        
    def render(self, delay=0.0, overlay=False):
        if overlay:
            import colorama
            colorama.init()

            def move_cursor(x, y):
                print ("\x1b[{};{}H".format(y + 1, x + 1))

            def clear():
                print ("\x1b[2J")

            clear()
            move_cursor(100, 100)
            np.set_printoptions(linewidth=100)
        bitmap = self._createBitmap()
        print(np.transpose(bitmap[:, :, 0] - bitmap[:, :, 1]))
        time.sleep(delay)

    def close(self):
        pass  # todo

    def seed(self, seed):
        self._parameters['seed']=seed

    def getState(self) -> FactoryFloorState:
        return self._state

    def setState(self, newState):
        self._state = copy.deepcopy(newState)

    @property
    def observation_space(self):
        """
        Returns 2 layers: first is for the robot positions, second for the task positions
        """
        obsSpace = spaces.MultiDiscrete([2, self._map.getWidth(), self._map.getHeight()]) 
        obsSpace.seed(self._parameters['seed'])
        return obsSpace

    @property
    def action_space(self):
        if USE_PossibleActionsSpace:
            actSpace = spaces.Dict({robot.getId():PossibleActionsSpace(self, robot) 
                for robot in self._state.robots})
        else:        
            actSpace = self._actSpace
        return actSpace
            
    ########## Getters ###############################
    
    def getMap(self):
        """
        @return: the map of this floor
        """
        return copy.deepcopy(self._map)

    def getPart(self, area:ndarray):  # -> FactoryFloor
        """
        @param area a numpy array of the form [[xmin,ymin],[xmax,ymax]]. 
        @return: A new FactoryFloor with same settings as this, but
        with Map#getPart(area) of this map, and only those bots and tasks that 
        are in that area. The new factoryfloor is completely independent of this floor.
        """
        parameters = copy.deepcopy(self._parameters)
        newmap = self._map.getPart(area)
        parameters['map'] = newmap.getFullMap()
        parameters['robots'] = [robot.getPosition().tolist() \
            for robot in self._state.robots if self._map.isInside(robot.getPosition())]
        parameters['tasks'] = [task.getPosition().tolist() \
            for task in self._state.tasks if self._map.isInside(task.getPosition())]
        parameters['P_task_appears'] = newmap.getTaskProbability()
        return FactoryFloor(parameters)
  
    def isPossible(self, robot:FactoryFloorRobot, action):
        """
        @param robot a FactoryFloorRobot
        @param action (integer) the action to be performed
        @return: true iff the action will be possible (can succeed) at this point.
        ACT is considered possible if there is a task at the current position.
        """
        pos = robot.getPosition()
        if action == 0:  # ACT
            return self._getTask(pos) != None
        return self._isFree(self._newPos(pos, action))
        
    def getPossibleActions(self, robot:FactoryFloorRobot):
        """
        @return the possible actions for the given robot on the floor
        """
        return [action for action in self.ACTIONS if self.isPossible(robot, action)]

    ########## Private functions ##########################

    def _createBitmap(self):
        bitmapRobots = np.zeros((self._map.getWidth(), self._map.getHeight()))
        bitmapTasks = np.zeros((self._map.getWidth(), self._map.getHeight()))
        for robot in self._state.robots:
            pos = robot.getPosition()
            bitmapRobots[pos[0], pos[1]] += 1

        for task in self._state.tasks:
            pos = task.getPosition()
            bitmapTasks[pos[0], pos[1]] += 1

        return dstack((9 * bitmapRobots, bitmapTasks))

    def _applyAction(self, robot, action):
        """
        robot tries to execute given action.
        @param robot a FactoryFloorRobot
        @param action the ACTION number. If ACT, then the robot executes all
        tasks that are in the tasks list and at the robot's location 
        """
        actstring = self.ACTIONS.get(action)
        try:
            if self._random.random() > self._parameters['P_action_succeed'][actstring]:
                return False
        except:
            pdb.post_mortem()

        pos = robot.getPosition()
        
        if actstring == "ACT":
            task = self._getTask(pos)
            if task != None:
                self._state.tasks.remove(task)
                logging.debug("removed " +str(task))
        else:  # move
            newpos = self._newPos(pos, action)
            if self._isFree(newpos):
                robot.setPosition(newpos)
  
    def _newPos(self, pos:ndarray, action):
        """
        @param pos the current (old) position of the robot (numpy array)
        @param action the action to be done in given position
        @return:  what would be the new position (ndarray) if robot did action.
        This does not check any legality of the new position, so the 
        position may run off the map or on a wall.
        """
        newpos = pos
        if self.ACTIONS.get(action) == "DOWN":
            newpos = pos + [0, 1]
        elif self.ACTIONS.get(action) == "RIGHT":
            newpos = pos + [1, 0]
        elif self.ACTIONS.get(action) == "UP":
            newpos = pos + [0, -1]
        elif self.ACTIONS.get(action) == "LEFT":
            newpos = pos + [-1, 0]
        return newpos
    
    def _getFreeMapPosition(self):
        """
        @return:random map position (x,y) that is not occupied by robot or wall.
        """
        while True:
            pos = self._map.getRandomPosition()
            if self._isFree(pos):
                return pos

    def _isRobot(self, position:ndarray):
        """
        @param position a numpy ndarray [x,y] that must be checked
        @return: true iff a robot occupies position
        """        
        for robot in self._state.robots:
            if (position == robot.getPosition()).all():
                return True
        return False

    def _isFree(self, pos:ndarray):
        """
        @return true iff the given pos has space for a robot,
        so it must be on the map and not on a wall and possibly
        not already containing a robot
        """
        return self._map.isInside(pos) and self._map.get(pos) != "*" \
            and (self._parameters['allow_robot_overlap'] or not(self._isRobot(pos)))
        
    def _addTask(self):
        """
        Add one new task to the task pool
        """
        poslist = self._map.getTaskPositions()
        if not self._parameters['allow_task_overlap']:
            if len(self._state.tasks) >= len(poslist):
                return

        # samplingSpace = spaces.MultiDiscrete([self._parameters['x_size'], self._parameters['y_size']])
        while True:  # do until newpos is not yet tasked, or task overlap allowed
            # work around numpy bug when list contains tuples
            i = weightedchoice(list(range(len(poslist))), 1, p=self._map.getTaskWeights())[0]
            newpos = poslist[i]
            if self._parameters['allow_task_overlap'] or self._getTask(newpos) == None:
                break;

        self._state.addTask(FactoryFloorTask(newpos))

    def _getTask(self, pos:tuple):
        """
        @return task at given position, or None if no task at position
        """
        for task in self._state.tasks:
            if (task.getPosition() == pos).all():
                return task
        return None

    def _computePenalty(self):
        penalty = 0
        for task in self._state.tasks:
            penalty += 1
        return penalty


class PossibleActionsSpace(FixedActionsSpace):
    """
    A gym space that returns the possible actions at this moment
    on the factory floor for some robot.
    REQUIREMENT: at all times, at least one action must be possible.
    If not, sample() may raise an exception
    NOTE this class is very tightly coupled to FactoryFloor.
    @param thefloor: the FactoryFloor
    @param bot: the FactoryFloorRobot
    """

    def __init__(self, fl:FactoryFloor, bot:FactoryFloorRobot):
        super().__init__()
        self._floor = fl
        self._robot = bot
    
    # Override
    def sample(self):
        return random.choice(self._floor.getPossibleActions(self._robot))
 
    # Override       
    def contains(self, act):
        return self._floor.isPossible(self._robot, act)
    
    # Override
    def getAllActions(self) -> dict:
        return self._floor.ACTIONS
    
