import torch
import torch.optim as optim
import torch.nn.functional as F
import torch.nn as nn
import pdb
import numpy as np

class PPOBase:
  def __init__(self, config):
    self.mem = config.memory()

    self.lr = config.lr
    self.n_steps = config.n_steps
    self.lr_annealing = config.lr_annealing
    self.epsilon_annealing = config.epsilon_annealing
    self.gamma = config.gamma
    self.epsilon = config.epsilon
    self.entropy_beta = config.entropy_beta
    self.device = config.device

    self.model = config.model(config).to(self.device)
    self.model_old = config.model(config).to(self.device)

    self.model_old.load_state_dict(self.model.state_dict())

    self.optimiser = optim.Adam(self.model.parameters(), lr=self.lr)

  def act(self, x):
    raise NotImplemented

  def add_to_mem(self, state, action, reward, log_prob, done):
    raise NotImplemented

  def learn(self, num_learn):
    raise NotImplemented

class PPOClassical(PPOBase):
  def __init__(self, config):
    super(PPOClassical, self).__init__(config)

  def act(self, x):
    x = torch.FloatTensor(x)
    return self.model_old.act(x)

  def add_to_mem(self, state, action, reward, log_prob, done):
    state = torch.FloatTensor(state)
    self.mem.add(state, action, reward, log_prob, done)

  def learn(self, num_learn):
    # Calculate discounted rewards
    discounted_returns = []
    running_reward = 0

    for reward, done in zip(reversed(self.mem.rewards), reversed(self.mem.dones)):
      if done:
        running_reward = 0
      running_reward = reward + self.gamma * running_reward

      discounted_returns.insert(0,running_reward)

    # normalise rewards
    discounted_returns = torch.FloatTensor(discounted_returns).to(self.device)
    discounted_returns = (discounted_returns - discounted_returns.mean()) / (discounted_returns.std() + 1e-5)

    prev_states = torch.stack(self.mem.states).to(self.device).detach()
    prev_actions = torch.stack(self.mem.actions).to(self.device).detach()
    prev_log_probs = torch.stack(self.mem.log_probs).to(self.device).detach()

    for i in range(num_learn):
      # find ratios
      actions, log_probs, values, entropy = self.model.act(prev_states, prev_actions)
      ratio = torch.exp(log_probs - prev_log_probs.detach())

      # calculate advantage
      advantage = discounted_returns - values

      # TODO: normalise advantages

      # calculate surrogates
      surrogate_1 = ratio * advantage
      surrogate_2 = torch.clamp(advantage, 1-self.epsilon, 1+self.epsilon)
      loss = -torch.min(surrogate_1, surrogate_2) + F.mse_loss(values, discounted_returns) - self.entropy_beta*entropy

      loss = loss.mean()

      # calculate gradient
      self.optimiser.zero_grad()
      loss.backward()
      self.optimiser.step()

    self.model_old.load_state_dict(self.model.state_dict())

class PPOPixel(PPOBase):
  def __init__(self, config):
    super(PPOPixel, self).__init__(config)
    self.config = config

  def state_shaper(self, state):
    state = np.array(state).transpose((2, 0, 1))
    state = torch.FloatTensor(state)
    state = state.unsqueeze(0)
    state = state.float() / 256

    return state

  def add_to_mem(self, state, action, reward, log_prob, done):
    state = self.state_shaper(state)
    self.mem.add(state, action, reward, log_prob, done)

  def act(self, x):
    x = self.state_shaper(x).to(self.config.device)
    return self.model_old.act(x)

  def learn(self, num_learn, last_value, next_done, global_step):
    # For reference: This is similar to how baselines and Costa are doing it.
    frac = 1.0 - (global_step - 1.0) / self.n_steps
    if self.lr_annealing:
      self.optimiser.param_groups[0]['lr'] = self.lr * frac
    epsilon_now = self.epsilon
    if self.epsilon_annealing:
      epsilon_now = self.epsilon * frac

    self.model_old.load_state_dict(self.model.state_dict())

    # Calculate discounted rewards
    bootstrap_length = self.config.update_every
    discounted_returns = torch.zeros(bootstrap_length)
    for t in reversed(range(bootstrap_length)):
      # If first loop
      if t == bootstrap_length - 1:
        nextnonterminal = 1.0 - next_done
        next_return = last_value
      else:
        nextnonterminal = 1.0 - self.mem.dones[t+1]
        next_return = discounted_returns[t+1]
      discounted_returns[t] = self.mem.rewards[t] + self.gamma * nextnonterminal * next_return

    # normalise rewards
    discounted_returns = torch.FloatTensor(discounted_returns).to(self.device)
    discounted_returns = (discounted_returns - discounted_returns.mean()) / (discounted_returns.std() + 1e-8)
    
    # Fetch collected state, action and log_probs from memory & reshape for nn
    prev_states = torch.stack(self.mem.states).reshape((-1,)+(4, 84, 84)).to(self.device).detach()
    prev_actions = torch.stack(self.mem.actions).reshape(-1).to(self.device).detach()
    prev_log_probs = torch.stack(self.mem.log_probs).reshape(-1).to(self.device).detach()

    for i in range(num_learn):
      # find ratios
      actions, log_probs, values, entropy = self.model.act(prev_states, prev_actions)
      ratio = torch.exp(log_probs - prev_log_probs.detach())
      values = values.squeeze() * 0.5

      # Stats
      approx_kl = (prev_log_probs - log_probs).mean()
      approx_entropy = entropy.mean()

      # calculate advantage & normalise
      advantage = discounted_returns - values
      advantage = (advantage - advantage.mean()) / (advantage.std() + 1e-8)

      # calculate surrogates
      surrogate_1 = ratio * advantage
      surrogate_2 = torch.clamp(advantage, 1 - epsilon_now, 1 + epsilon_now)

      # Calculate losses
      values_clipped = last_value + torch.clamp(values - last_value, -epsilon_now, epsilon_now)
      value_loss_unclipped = F.mse_loss(values, discounted_returns)
      value_loss_clipped = F.mse_loss(values_clipped, discounted_returns)
      value_loss = .5 * torch.mean(torch.max(value_loss_clipped, value_loss_unclipped))
      pg_loss = -torch.min(surrogate_1, surrogate_2).mean()

      loss = pg_loss + value_loss - self.entropy_beta*entropy
      loss = loss.mean()

      # calculate gradient
      self.optimiser.zero_grad()
      loss.backward()
      nn.utils.clip_grad_norm_(self.model.parameters(), 0.5)
      self.optimiser.step()

      if torch.abs(approx_kl) > 0.03:
        break

      _, new_log_probs, _, _ = self.model.act(prev_states, prev_actions)
      if (prev_log_probs - new_log_probs).mean() > 0.03:
        self.model.load_state_dict(self.model_old.state_dict())
        break

    return value_loss, pg_loss, approx_kl, approx_entropy
