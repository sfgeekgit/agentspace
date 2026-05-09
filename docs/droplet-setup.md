# Droplet Setup Recipe

Fresh Debian 13 (trixie) DO droplet. Run as a user with sudo. Assumes two users: `cc` (main) and `nick`.

---

## 1. Install Docker

```bash
sudo apt-get update -qq
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -qq
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Add users to docker group (requires logout/login to take effect):
```bash
sudo usermod -aG docker cc
sudo usermod -aG docker nick
```

Verify:
```bash
sudo docker run hello-world
```

---

## 2. Install Node.js 24

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt-get install -y nodejs
```

Verify:
```bash
node --version   # expect v24.x
npm --version
```

---

## 3. Install OpenClaw

```bash
sudo npm install -g openclaw@latest
```

Verify:
```bash
openclaw --version
```

---

## 4. Install Python 3 + SQLite

Python 3 is pre-installed on Debian 13. SQLite needs to be added:
```bash
sudo apt-get install -y sqlite3
```

Verify:
```bash
python3 --version
sqlite3 --version
```

---

## 5. Git Config (both users)

```bash
# cc
git config --global user.name "sfgeekgit"
git config --global user.email "sfgeek@gmail.com"

# nick
sudo -u nick git config --global user.name "sfgeekgit"
sudo -u nick git config --global user.email "sfgeek@gmail.com"
```

---

## 6. SSH Key for GitHub (shared between cc and nick)

Generate one key for `cc`, copy to `nick`:
```bash
# Generate for cc
mkdir -p ~/.ssh
ssh-keygen -t ed25519 -C "sfgeek@gmail.com" -f ~/.ssh/github_ed25519 -N ""

# Configure SSH for GitHub (cc)
echo "Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_ed25519
  IdentitiesOnly yes" >> ~/.ssh/config
chmod 600 ~/.ssh/config

# Copy to nick
sudo cp /home/cc/.ssh/github_ed25519 /home/nick/.ssh/github_ed25519
sudo cp /home/cc/.ssh/github_ed25519.pub /home/nick/.ssh/github_ed25519.pub
sudo chown nick:nick /home/nick/.ssh/github_ed25519 /home/nick/.ssh/github_ed25519.pub
sudo chmod 600 /home/nick/.ssh/github_ed25519

# Configure SSH for GitHub (nick)
sudo -u nick bash -c 'echo "Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_ed25519
  IdentitiesOnly yes" >> ~/.ssh/config && chmod 600 ~/.ssh/config'
```

Print public key to add to GitHub (Settings → SSH keys → New SSH key):
```bash
cat ~/.ssh/github_ed25519.pub
```

Verify both users:
```bash
ssh -T git@github.com -o StrictHostKeyChecking=accept-new
sudo -u nick ssh -T git@github.com -o StrictHostKeyChecking=accept-new
# Expected: "Hi sfgeekgit! You've successfully authenticated..."
```

---

## 7. OpenClaw Onboarding (interactive)

Run once per user who will use the Gateway directly. For this project, the Gateway runs inside Docker containers — see the project repo for env setup. But to test on the host:

```bash
openclaw onboard --auth-choice openrouter-api-key
```

Wizard will ask for your OpenRouter key. Config lands at `~/.openclaw/openclaw.json`.

To verify:
```bash
openclaw gateway status
openclaw tui
```

---

## What's next (project-specific)

See the project repo for:
- Dockerfile (env image)
- Python control CLI (`fork_env`, `list_envs`, etc.)
- Agent configs and SOUL.md files
- Scenario definitions
