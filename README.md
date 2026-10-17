# Web Application & API Penetration Testing (DEPI)

An agentic AI assistant engineered for authorized web application penetration testing, API vulnerability assessment, bug bounty automation, and security research — operable directly from your terminal.

---

##  System Requirements

The toolkit utilizes a modern asynchronous runtime and a modular agent pipeline designed to interface securely with local or cloud-based Large Language Models (LLMs).

- **Node.js**: `v20.0.0+` — [Download Node.js](https://nodejs.org)
- **Bun Runtime**: Used for high-performance bundling and terminal user interface (TUI) execution — [Download Bun](https://bun.sh)
- **LLM Backend**:
  - **Local Inference**: [Ollama](https://ollama.com) (free, fully offline)
  - **Cloud Inference**: Any OpenAI-compatible API endpoint (OpenRouter, Groq, Gemini, DeepSeek, etc.)

---

##  Installation

Clone the repository and install the project dependencies:

```bash
git clone https://github.com/mo7amedsalem1/Web-Application-API-Penetration-Testing-DEPI-.git
cd Web-Application-API-Penetration-Testing-DEPI-
npm install
```

---

##  Build Standalone Binary

Compile the application into a single standalone binary containing the embedded runtime environment:

### Windows
```powershell
bun build src/cli/index.ts --compile --outfile app.exe
```

### macOS / Linux
```bash
bun build src/cli/index.ts --compile --outfile app
```

*(Optional)* Move the compiled executable to a directory in your system's `PATH` to execute it globally.

---

##  Usage & Execution Modes

### 1. Interactive TUI Launch
```bash
./app
```

### 2. Local Model (Ollama)
Ensure Ollama is running locally, pull your preferred model, and launch:
```bash
ollama pull qwen3:4b
./app --model qwen3:4b
```

### 3. Cloud Model
Execute using an external API provider (e.g., OpenRouter with Claude 3.7 Sonnet):
```bash
./app --backend openrouter --model anthropic/claude-3.7-sonnet --api-key sk-or-...
```

---

##  CLI Options Reference

| Option / Flag | Description |
| :--- | :--- |
| `./app` | Launches the interactive Terminal User Interface (TUI). |
| `--model <name>` | Specifies the active LLM engine/model to use. |
| `--burp <port>` | Starts the local HTTP/S proxy bridge (e.g., Burp Suite on port `9999`). |
| `--help` | Displays all available commands and flags. |
