import math
import sys
import unittest
from collections import deque

from pettingzoo.utils.env import ParallelEnv
from pettingzoo.utils import wrappers
import numpy as np
import random
import csv
import torch
import torch.optim as optim

from agent import Agent
from model import DDQNLSTM

class PredatorPreyEnv(ParallelEnv):
    def __init__(self, grid_size=(15, 15), num_predators=2, num_prey=3, num_walls=5, predator_scope=2, health_gained=0.3):
        """
        Initializes the environment.
        grid_size: Tuple[int, int] - dimensions of the grid.
        num_predators: int - number of predator agents.
        num_prey: int - number of prey agents.
        num_walls: int - number of wall elements.
        predator_scope: int - range of predator, where preys are killed
        health_gained: float - value of health restored with killing a prey
        """
        self.grid_size = grid_size
        self.num_predators = num_predators
        self.num_prey = num_prey
        self.num_walls = num_walls
        self.predator_scope = predator_scope
        self.health_gained = health_gained

        self.max_num_predators = 10000
        self.max_num_preys = 10000

        self.agents = []
        # self.agent_positions = {agent: None for agent in self.agents}
        # self.agent_health = {agent: 1 for agent in self.agents}
        self.walls_positions = []

        # Initialize the grid
        self.grid = np.zeros(self.grid_size, dtype=object)

        # self.reset()

    def reset(self):
        """Resets the environment."""
        self.grid.fill(0)
        self.walls_positions.clear()

        # Place walls
        for _ in range(self.num_walls):
            while True:
                x, y = random.randint(0, self.grid_size[0] - 1), random.randint(0, self.grid_size[1] - 1)
                if self.grid[x, y] == 0:
                    self.grid[x, y] = -1  # Wall
                    self.walls_positions.append((x, y))
                    break

        # Create and place predators
        for i in range(self.num_predators):
            while True:
                x, y = random.randint(0, self.grid_size[0] - 1), random.randint(0, self.grid_size[1] - 1)
                if self.grid[x, y] == 0:
                    predator = Agent(f"pr_{i}", "predator", (x, y))
                    self.agents.append(predator)
                    self.grid[x, y] = predator  # Predator
                    break

        # Create and place prey
        for i in range(self.num_prey):
            while True:
                x, y = random.randint(0, self.grid_size[0] - 1), random.randint(0, self.grid_size[1] - 1)
                if self.grid[x, y] == 0:
                    prey = Agent(f"py_{i}", "prey", (x, y))
                    self.agents.append(prey)
                    self.grid[x, y] = prey  # Prey
                    break

        return {agent.id: self.get_observation(agent) for agent in self.agents}

    def agents_move(self, actions):
        """Make a move of each agent"""
        new_positions = {}

        for agent in self.agents:
            x, y = agent.get_position()
            new_x, new_y = x, y

            # random actions for now
            action = actions[agent.id]

            if action == 1:  # up
                new_x = (x - 1) % self.grid_size[0]
            elif action == 2:  # down
                new_x = (x + 1) % self.grid_size[0]
            elif action == 3:  # left
                new_y = (y - 1) % self.grid_size[1]
            elif action == 4:  # right
                new_y = (y + 1) % self.grid_size[1]

            if self.grid[new_x, new_y] == 0:  # Move if the cell is empty
                new_positions[agent.id] = (new_x, new_y)
            else:  # Stay in place if the cell is occupied
                new_positions[agent.id] = (x, y)

        # Update grid and agent positions
        self.grid.fill(0)
        for wall in self.walls_positions:
            self.grid[wall[0], wall[1]] = -1

        for agent in self.agents:
            x, y = new_positions[agent.id]
            self.grid[x, y] = agent
            agent.set_position((x, y))

    # TODO try to optimise with object pointers
    def hunting(self, rewards, dones):
        """Handle predator prey interaction - hunting"""
        for predator in [a for a in self.agents if "predator" in a.role]:
            px, py = predator.get_position()
            prey_in_scope = []

            for dx in range(-self.predator_scope, self.predator_scope + 1):
                for dy in range(-self.predator_scope, self.predator_scope + 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = (px + dx) % self.grid_size[0], (py + dy) % self.grid_size[1]
                    if type(self.grid[nx, ny]) == Agent and self.grid[nx, ny].role == 'prey':
                        distance = abs(dx) + abs(dy)  # Manhattan
                        prey_in_scope.append((distance, (nx, ny)))

            if prey_in_scope:
                # Kill the nearest prey
                prey_in_scope.sort()
                target_prey_pos = prey_in_scope[0][1]
                for prey in self.agents:
                    pos = prey.get_position()
                    if pos == target_prey_pos:
                        self.agents.remove(prey)
                        self.grid[target_prey_pos[0], target_prey_pos[1]] = 0
                        rewards[predator.id] += 1  # Reward for eating prey
                        rewards[prey.id] += -1
                        predator.add_health(self.health_gained)  # Add constant value
                        dones[prey.id] = True
                        # print(f'{prey.id} killed')
                        break

        return rewards, dones

    def predator_hunger(self, dones):
        """Decrease predator health and remove dead predators"""
        for predator in [a for a in self.agents if "predator" in a.role]:
            predator.add_health(-0.01)
            if predator.health <= 0:
                px, py = predator.get_position()
                self.agents.remove(predator)
                self.grid[px, py] = 0
                dones[predator.id] = True
                # print(f'{predator.id} killed')
        return dones

    def generate_new_agents(self, p_predator=0.003, p_prey=0.006):
        """
        Generates new predators and prey based on the provided formula.
        p_predator: float - probability factor for generating new predators.
        p_prey: float - probability factor for generating new prey.
        """
        # Calculate the number of new predators and prey
        num_predators = len([a for a in self.agents if "predator" in a.role])
        num_preys = len([a for a in self.agents if "prey" in a.role])

        new_preys = 0
        new_predators = 0
        if num_predators < self.max_num_predators:
            new_predators = max(1, math.ceil(num_predators * p_predator))
        if num_preys < self.max_num_preys:
            new_preys = max(1, math.ceil(num_preys * p_prey))

        # Add new predators
        for _ in range(new_predators):
            predator_id = f"pr_{len([a for a in self.agents if 'predator' in a.role])}"
            while True:
                x, y = random.randint(0, self.grid_size[0] - 1), random.randint(0, self.grid_size[1] - 1)
                if self.grid[x, y] == 0:  # Empty cell
                    created_agent = Agent(predator_id, 'predator', (x, y))
                    self.grid[x, y] = created_agent  # Predator
                    self.agents.append(created_agent)
                    break

        # Add new preys
        for _ in range(new_preys):
            prey_id = f"py_{len([a for a in self.agents if 'prey' in a.role])}"
            while True:
                x, y = random.randint(0, self.grid_size[0] - 1), random.randint(0, self.grid_size[1] - 1)
                if self.grid[x, y] == 0:  # Empty cell
                    created_agent = Agent(prey_id, 'prey', (x, y))
                    self.grid[x, y] = created_agent  # Prey
                    self.agents.append(created_agent)
                    break

    def step(self, actions):
        """Takes a step in the environment based on the actions and environment rules."""
        rewards = {agent.id: 0 for agent in self.agents}
        dones = {agent.id: False for agent in self.agents}

        self.agents_move(actions)

        rewards, dones = self.hunting(rewards, dones)

        dones = self.predator_hunger(dones)

        self.generate_new_agents(0.003, 0.006)
        # Update observations
        observations = {agent.id: self.get_observation(agent) for agent in self.agents}

        return observations, rewards, dones

    def get_observation(self, agent):
        """Returns a 4-channel local grid observation for the given agent."""
        ax, ay = agent.get_position()
        size = self.predator_scope * 2 + 1

        wall_layer = np.zeros((size, size), dtype=int)
        predator_layer = np.zeros((size, size), dtype=int)
        prey_layer = np.zeros((size, size), dtype=int)
        health_layer = np.zeros((size, size), dtype=float)

        for dx in range(-self.predator_scope, self.predator_scope + 1):
            for dy in range(-self.predator_scope, self.predator_scope + 1):
                nx, ny = (ax + dx) % self.grid_size[0], (ay + dy) % self.grid_size[1]
                local_x, local_y = dx + self.predator_scope, dy + self.predator_scope

                if self.grid[nx, ny] == -1:
                    wall_layer[local_x, local_y] = 1
                elif type(self.grid[nx, ny]) == Agent and self.grid[nx, ny].role == 'predator':
                    predator_layer[local_x, local_y] = 1
                    health_layer[local_x, local_y] = self.grid[nx, ny].health
                elif type(self.grid[nx, ny]) == Agent and self.grid[nx, ny].role == 'prey':
                    prey_layer[local_x, local_y] = 1
                    health_layer[local_x, local_y] = self.grid[nx, ny].health


        return np.stack([wall_layer, predator_layer, prey_layer, health_layer], axis=0)

    def render(self):
        """Renders the environment in the console."""
        render_grid = np.full(self.grid.shape, '.')

        render_grid[self.grid == -1] = '#'  # Wall
        render_grid[self.grid == 1] = 'O'  # Prey
        render_grid[self.grid == 2] = 'X'  # Predator

        print("\n".join("".join(row) for row in render_grid))
        print()


def batchify(data, batch_size):
    return [data[i:i + batch_size] for i in range(0, len(data), batch_size)]


def update_weights(agent_replay_buffer, agent_policy_model, agent_target_model, agent_optimizer, device='cpu'):
    batch = random.sample(agent_replay_buffer, BUFFER_SIZE)

    mini_batches = batchify(batch, BATCH_SIZE)
    for minibatch in mini_batches:
        # Prepare batches for training
        # obs_batch = torch.tensor([exp[0] for exp in minibatch], dtype=torch.float32)
        # action_batch = torch.tensor([exp[1] for exp in minibatch], dtype=torch.long)
        # reward_batch = torch.tensor([exp[2] for exp in minibatch], dtype=torch.float32)
        # done_batch = torch.tensor([exp[3] for exp in minibatch], dtype=torch.float32)
        # next_obs_batch = torch.empty(len(minibatch), 4, 11, 11, dtype=torch.float32)  # Allocate an empty tensor
        # for ii, exp in enumerate(minibatch):
        #     next_obs_batch[ii] = torch.tensor(exp[4])
        # # next_obs_batch = torch.tensor([exp[4] for exp in minibatch], dtype=torch.float32)
        # hidden_state_batch = [exp[5] for exp in minibatch]
        # new_hidden_state_batch = [exp[6] for exp in minibatch]

        # Compute target Q-values and optimize
        q_values_batch = []
        target_q_values = []
        for obs_mn, action_mn, reward_mn, done_mn, next_obs_mn, hidden_state_mn, next_hidden_state_mn in minibatch:
            with torch.no_grad():
                next_obs = torch.tensor(next_obs_mn, dtype=torch.float32).unsqueeze(0).to(device)
                next_action = torch.argmax(agent_policy_model(next_obs, next_hidden_state_mn)[0])
                target_q_value = reward_mn + GAMMA * (1 - done_mn) * \
                                 agent_target_model(next_obs, next_hidden_state_mn)[0].squeeze(0)[next_action]
                target_q_values.append(target_q_value)
            q_values, _ = agent_policy_model(torch.tensor(obs_mn, dtype=torch.float32, device=device).unsqueeze(0), hidden_state_mn)
            q_value = q_values.gather(1, action_mn.view(1, 1)).squeeze()
            q_values_batch.append(q_value)
        target_q_values = torch.stack(target_q_values)

        q_values_batch = torch.stack((q_values_batch))
        # Compute current Q-values and loss
        # if all(x is None for x in hidden_state_batch):
        #     q_values, _ = agent_policy_model(obs_batch)
        # else:
        #     q_values, _ = agent_policy_model(obs_batch, hidden_state_batch)
        # q_values = q_values.gather(1, action_batch.unsqueeze(1)).squeeze()
        loss = torch.nn.functional.mse_loss(q_values_batch, target_q_values)

        # Optimize the shared network
        agent_optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(agent_policy_model.parameters(), 1.0)
        agent_optimizer.step()

    if i % UPDATE_FREQ == 0:
        agent_target_model.load_state_dict(agent_policy_model.state_dict())
    agent_replay_buffer.clear()
# Wrapping the environment - Can be added in the future

def env_creator():
    env = PredatorPreyEnv((600, 600), 1000, 1000, 1000, 5, 0.7)
    return env

RUN_TESTS_BEFORE = False

def run_tests():
    print("Running tests...")
    
    test_suite = unittest.defaultTestLoader.discover(start_dir='.', pattern='test_*.py')
    test_runner = unittest.TextTestRunner()
    result = test_runner.run(test_suite)

    if not result.wasSuccessful():
        print("Tests failed! The program will be terminated...")
        sys.exit(1)
    else:
        print("All tests passed! Proceeding to main program...")

# Example usage
if __name__ == "__main__":
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    if RUN_TESTS_BEFORE:
        run_tests() 
    else:
        print("WARNING: running without tests...")    

    # Hyperparameters
    BUFFER_SIZE = 64
    BATCH_SIZE = 64
    EPSILON = 0.1
    UPDATE_FREQ = 50
    GAMMA = 0.99
    LEARNING_RATE = 0.0001

    env = env_creator()
    obs = env.reset()
    # env.render()

    csv_file = 'output_ENV_1_more_hunger_ceil.csv'
    data = []

    predator_replay_buffer = deque()
    prey_replay_buffer = deque()

    # Models
    predator_policy_model = DDQNLSTM((4, 11, 11), 4).to(device)
    predator_target_model = DDQNLSTM((4, 11, 11), 4).to(device)
    prey_policy_model = DDQNLSTM((4, 11, 11), 4).to(device)
    prey_target_model = DDQNLSTM((4, 11, 11), 4).to(device)

    # Optimizers
    predator_optimizer = optim.Adam(predator_policy_model.parameters(), lr=LEARNING_RATE)
    prey_optimizer = optim.Adam(prey_policy_model.parameters(), lr=LEARNING_RATE)

    hidden_states = {agent.id: None for agent in env.agents}
    new_hidden_states = {agent.id: None for agent in env.agents}

    for i in range(20000):
        actions = {}
        # actions = {agent.id: random.randint(0, 4) for agent in env.agents}
        for agent in env.agents:
            obs_tensor = torch.tensor(obs[agent.id], dtype=torch.float32).unsqueeze(0).to(device)
            if agent.id not in hidden_states.keys():
                hidden_state = None
                hidden_states[agent.id] = None
            else:
                hidden_state = hidden_states[agent.id]
            if agent.role == 'predator':
                action_values, new_hidden_state = predator_policy_model(obs_tensor, hidden_state)
            else:
                action_values, new_hidden_state = prey_policy_model(obs_tensor, hidden_state)

            if random.random() < EPSILON:  # Exploration
                actions[agent.id] = torch.tensor(random.randint(0, 3), device=device)  # Assuming action space is [0, 1, 2, 3]
            else:  # Exploitation
                actions[agent.id] = torch.argmax(action_values)
            new_hidden_states[agent.id] = new_hidden_state

        new_obs, rewards, dones = env.step(actions)

        # experiences["observations"].update(obs)
        # experiences["actions"].update(actions)
        # experiences["rewards"].update(rewards)
        # experiences["dones"].update(dones)
        for agent_id in actions.keys():
            if dones[agent_id]:
                new_obs_to_save = torch.zeros_like(torch.tensor(obs[agent_id], dtype=torch.float32)).to(device)  # Placeholder
            else:
                new_obs_to_save = new_obs[agent_id]
            experience = (
                obs[agent_id],  # Current observation
                actions[agent_id],  # Action taken
                rewards[agent_id],  # Reward received
                dones[agent_id],  # Done flag
                new_obs_to_save,  # Next observation
                hidden_states[agent_id],  # Current hidden state
                new_hidden_states[agent_id]
            )
            if agent_id[:2] == 'pr':
                predator_replay_buffer.append(experience)
            else:
                prey_replay_buffer.append(experience)

        # env.generate_new_agents()
        if len(predator_replay_buffer) >= BUFFER_SIZE:
            # Sample a minibatch and train (same as before)
            update_weights(predator_replay_buffer, predator_policy_model, predator_target_model, predator_optimizer, device)
        if len(prey_replay_buffer) >= BUFFER_SIZE:
            # Sample a minibatch and train (same as before)
            update_weights(prey_replay_buffer, prey_policy_model, prey_target_model, prey_optimizer, device)


        num_predators = len([a for a in env.agents if "predator" in a.role])
        num_preys = len([a for a in env.agents if "prey" in a.role])
        data.append([i, num_predators, num_preys])

        obs = new_obs
        hidden_state = new_hidden_states
        print(i, num_predators, num_preys)
        with open(csv_file, mode='a', newline='') as file:  # Open in append mode
            writer = csv.writer(file)
            writer.writerow([i, num_predators, num_preys])
    torch.save(predator_target_model.state_dict(), "predator_target_model.pth")
    torch.save(predator_policy_model.state_dict(), "predator_policy_model.pth")

    torch.save(prey_target_model.state_dict(), "prey_target_model.pth")
    torch.save(prey_policy_model.state_dict(), "prey_policy_model.pth")
        # env.render()

    # with open(csv_file, mode='w', newline='') as file:
    #     writer = csv.writer(file)
    #
    #     # Loop through each item in data
    #     for row in data:
    #         # Extract the last three elements
    #         last_three = row[-3:]
    #
    #         # Write them to the CSV
    #         writer.writerow(last_three)


