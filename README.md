NIFTY Options Agent — Project README

Purpose
- Concise summary of the repository and how to ask the assistant to load this README at the start of new chats.

Quick overview
- baseline_v1_live/: Main trading system (swing detection, continuous filtering, proactive SL orders, position tracking, SQLite persistence).
- openalgo-zerodha/: Broker integration layer (OpenAlgo API + WebSocket proxy for Zerodha and other brokers).
- docker-compose.yaml, Dockerfile, deploy scripts: For local and EC2 deployment.

Key files
- baseline_v1_live/README.md: Detailed system docs and implementation notes.
- DOCKER_COMMANDS.md, README_DOCKER.md, DOCKER_DEPLOY.md: Docker deployment and operation guides.
- PRE_PUSH_CHECKLIST.md: Pre-commit checks and guidelines.
- D:\nifty_options_agent\README.md: This high-level entrypoint summary (you are reading it).

How to use with the assistant
- To ask the assistant to read and use this README at the start of any new chat, include the exact line as the first user message:

    Please load project README.md

  The assistant will then open and use this file as context before making changes. Additionally, the assistant will automatically read all top-level .md files if asked to "read all .md files" in a session.

Updating this file
- Edit D:\nifty_options_agent\README.md in the repo and commit changes; keep it succinct and up-to-date.

Contact
- For implementation details and code walkthroughs, ask the assistant in the same session or request a fresh "explore project structure" analysis.
