import functools
import gym
from Config import Config
from util import train_pixel
from Models import ActorCritic
from Networks import cnn_head_model, actor_model, critic_model, head_model
from Memory import Memory
import time
from torch.utils.tensorboard import SummaryWriter
from baselines.common.atari_wrappers import wrap_deepmind, make_atari

import matplotlib.pyplot as plt

env_id = "BreakoutNoFrameskip-v4"
env = make_atari(env_id)
env = wrap_deepmind(env, episode_life=True, clip_rewards=True, frame_stack=True, scale=False)

config = Config(env)

config.total_global_steps = 10e6
config.update_every = 2000
config.num_learn = 4
config.win_condition = 230
config.n_episodes = 2000
config.max_t = 2000
config.hidden_size = 512
config.lr_annealing = True

config.Memory = Memory
config.Model = ActorCritic
config.head_model = functools.partial(cnn_head_model, config)
config.actor_model = functools.partial(actor_model, config)
config.critic_model = functools.partial(critic_model, config)

experiment_name = f"{env_id}____{int(time.time())}"
config.tb_logger = SummaryWriter(f"runs/{experiment_name}")

scores = train_pixel(config)
plt.plot(scores)
plt.show()
