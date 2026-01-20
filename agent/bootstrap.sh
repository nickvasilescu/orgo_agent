#!/bin/bash
# bootstrap.sh - Set up Ralph Wiggum agent on Orgo VM
# This script is run once during workspace_register to prepare the VM

set -e

echo "=== Ralph Wiggum Bootstrap Script ==="
echo "Starting at $(date)"

# Update and install system dependencies
echo "Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3-pip python3-venv git curl wget xvfb

# Create virtual environment for agent
echo "Creating Python virtual environment..."
python3 -m venv ~/agent_env
source ~/agent_env/bin/activate

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -q \
    anthropic \
    browser-use \
    playwright \
    langchain-anthropic \
    pydantic \
    httpx \
    python-dotenv

# Install Playwright browsers
echo "Installing Playwright browsers..."
playwright install chromium
playwright install-deps chromium

# Create working directories
echo "Creating working directories..."
mkdir -p ~/workspace ~/logs ~/vault

# Create tasks.md if it doesn't exist (Ralph polls this)
touch ~/workspace/tasks.md

# Create agent runner script
echo "Creating agent runner script..."
cat > ~/run_ralph.sh << 'RUNNER'
#!/bin/bash
source ~/agent_env/bin/activate
cd ~

# Load environment variables
if [ -f ~/.env ]; then
    export $(cat ~/.env | grep -v '^#' | xargs)
fi

# Run Ralph Wiggum with restart on crash
while true; do
    echo "Starting Ralph Wiggum at $(date)"
    python3 ~/ralph_wiggum.py >> ~/logs/ralph.log 2>&1

    exit_code=$?
    echo "Ralph Wiggum exited with code $exit_code at $(date)"

    if [ $exit_code -eq 0 ]; then
        echo "Clean exit, not restarting"
        break
    fi

    echo "Restarting in 5 seconds..."
    sleep 5
done
RUNNER

chmod +x ~/run_ralph.sh

# Create systemd service for Ralph (optional, for auto-start)
echo "Creating systemd service..."
cat > /etc/systemd/system/ralph.service << SERVICE
[Unit]
Description=Ralph Wiggum Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
ExecStart=/root/run_ralph.sh
Restart=always
RestartSec=5
Environment=DISPLAY=:99

[Install]
WantedBy=multi-user.target
SERVICE

# Start Xvfb for headless browser
echo "Starting Xvfb..."
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99

# Reload systemd
systemctl daemon-reload

echo "=== Bootstrap Complete ==="
echo "Finished at $(date)"
echo ""
echo "Next steps:"
echo "1. Upload ralph_wiggum.py to ~/ralph_wiggum.py"
echo "2. Create ~/.env with ANTHROPIC_API_KEY"
echo "3. Start agent: systemctl start ralph"
echo "   Or manually: ~/run_ralph.sh &"
