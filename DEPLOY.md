# DEPLOY.md — Jarvis on Oracle Always Free (plain English, ~30 min)

**Big picture:** Oracle gives you a free computer that runs 24/7. Jarvis moves there; your laptop and phone just visit it. **No Oracle credentials go into any project file** — Oracle is only the landlord. The only secrets are your Gemini key + the access token, both in `.env` ON the VM.

## A. Create the account (one-time)
1. Go to **https://www.oracle.com/cloud/free/** → Start for free.
2. Email + password + address. **Card required for identity check only** — Always Free resources are not charged. Pick the home region closest to you (e.g. Mumbai/Hyderabad) — it cannot change later.
3. Verify email, sign in to the Cloud Console.

## B. Create the VM
1. Console → **Compute → Instances → Create instance**.
2. Image: **Ubuntu 24.04**, Shape: **Ampere ARM (VM.Standard.A1.Flex)** → **2 OCPU / 12 GB** (Always Free-eligible). If "out of capacity": retry later or try another availability domain; fallback = any ~$4 Ubuntu VPS, same script.
3. **Save the SSH key pair** it offers (download both files). The **private key stays on your laptop only** — never upload it anywhere.
4. Boot volume: default is fine (up to 200 GB free total). Create.
5. Note the **public IP**.

## C. Open the doors (ports 80 + 443)
1. Instance page → subnet → **Security List → Add Ingress Rules**: Source `0.0.0.0/0`, TCP, destination port **80**; repeat for **443**.
2. The setup script also opens them inside Ubuntu (Oracle images block by default).

## D. Upload the project (from your laptop terminal)
Files to upload = the whole brain: **all 9 `.py` files** (server, session_manager, database, registry, tools, calculators, assembler, models, text_stream), **`static/`** (hud.html, setup.html, dashboard.html), **`requirements.txt`**, **`deploy/`**. NOT needed: venv, old DBs (fresh start), exit tests, `.env` (you'll make a new one on the VM).
```bash
chmod 400 ~/Downloads/ssh-key-*.key          # private key permissions
ssh -i ~/Downloads/ssh-key-*.key ubuntu@YOUR_IP "mkdir -p ~/jarvis/static ~/jarvis/deploy"
cd ~/Downloads/Butler3.0
scp -i ~/Downloads/ssh-key-*.key *.py requirements.txt ubuntu@YOUR_IP:~/jarvis/
scp -i ~/Downloads/ssh-key-*.key static/hud.html static/setup.html static/dashboard.html ubuntu@YOUR_IP:~/jarvis/static/
scp -i ~/Downloads/ssh-key-*.key deploy/setup-oracle.sh ubuntu@YOUR_IP:~/jarvis/deploy/
```

## E. Run the setup (on the VM)
```bash
ssh -i ~/Downloads/ssh-key-*.key ubuntu@YOUR_IP
cd ~/jarvis && bash deploy/setup-oracle.sh
nano .env        # paste your Gemini key after GEMINI_API_KEY=  (Ctrl+O, Enter, Ctrl+X)
sudo systemctl restart jarvis
```
The script prints your **access token** — save it; the HUD lock screen asks for it.

## F. Test
Open **https://YOUR_IP.sslip.io** on your phone → enter token → wizard (first boot) → type + speak. Laptop closed, Jarvis answers. 🎩

## Credentials map (what goes where)
| Credential | Where it lives | Goes in a project file? |
|---|---|---|
| Oracle email/password/card | oracle.com only | **No** |
| SSH private key | your laptop only | **No** |
| VM public IP + user `ubuntu` | your notes / ssh command | No (script detects IP) |
| `GEMINI_API_KEY` | `.env` **on the VM** | .env only, never chat/APK |
| `JARVIS_ACCESS_TOKEN` | `.env` on VM (script generates) | .env only |

**Troubleshooting:** site not loading → ports (step C + script's iptables line) · `sudo journalctl -u jarvis -n 50` for server logs · `sudo systemctl status caddy` for HTTPS.
