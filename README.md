<div align="center">

# hackerclaw

**An agentic AI assistant for authorized penetration testing, bug bounty, and security research — right in your terminal.**

</div>

---

## Requirements

- **Node.js 20+** — https://nodejs.org
- **Bun** — https://bun.sh  (used to build the terminal UI)
- **A model backend**, either:
  - **Ollama** (local, free) — https://ollama.com, or
  - a cloud API key (OpenRouter / OpenAI-compatible / Groq / Gemini / DeepSeek …)

## Install

```bash
git clone https://github.com/mo7amedsalem1/Web-Application-API-Penetration-Testing-DEPI-
cd Web-Application-API-Penetration-Testing-DEPI-
npm install
```

## Build

Compile the standalone binary (bundles the runtime — nothing else needed to run it):

```bash
# Windows
bun build src/cli/index.ts --compile --outfile hackerclaw.exe

# macOS / Linux
bun build src/cli/index.ts --compile --outfile hackerclaw
```

Optionally move the resulting binary somewhere on your `PATH` so you can call `hackerclaw` from anywhere.

## Run

```bash
# Windows
.\hackerclaw.exe

# macOS / Linux
./hackerclaw
```

### Use a local model (Ollama)

```bash
ollama pull qwen3:4b
hackerclaw --model qwen3:4b
```

### Use a cloud model

```bash
hackerclaw --backend openrouter --model anthropic/claude-3.7-sonnet --api-key sk-or-...
```

### Common commands

```bash
hackerclaw                    # launch the interactive TUI
hackerclaw --model <name>     # pick the model
hackerclaw --burp 9999        # start the local Burp bridge
hackerclaw --help             # list all flags
```

---
