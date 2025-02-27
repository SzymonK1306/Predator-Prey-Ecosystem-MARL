import torch.nn as nn
import torch.nn.functional as F


class DDQNLSTM(nn.Module):
    def __init__(self, input_shape, n_actions):
        super(DDQNLSTM, self).__init__()

        # Convolutional layers with padding to preserve dimensions
        self.conv1 = nn.Conv2d(in_channels=input_shape[0], out_channels=32, kernel_size=4, stride=4)
        self.conv2 = nn.Conv2d(in_channels=32, out_channels=64, kernel_size=3, stride=2)
        self.conv3 = nn.Conv2d(in_channels=64, out_channels=256, kernel_size=3, stride=2)

        # LSTM layer
        self.lstm = nn.LSTM(input_size=256, hidden_size=256, batch_first=True)

        # Fully connected layers for state-value and advantage-value
        self.fc_output_layer = nn.Linear(256, 128)
        self.output_layer = nn.Linear(128, n_actions)

    def forward(self, x, hidden_state=None):
        batch_size = x.size(0)

        # Convolutional layers
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))

        # Flatten for LSTM input
        x = x.view(batch_size, -1)
        x = x.unsqueeze(1)

        # LSTM layer
        if hidden_state is None:
            x, hidden_state = self.lstm(x)
        else:
            x, hidden_state = self.lstm(x, hidden_state)

        x = x.squeeze(1)  # Remove the time dimension

        # State-value stream
        state = F.relu(self.fc_output_layer(x))
        output = self.output_layer(state)

        return output, hidden_state
