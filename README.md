# Video Template Replicator

An AI Agent Skill that recreates reference video templates (grid reveals, photo/video slideshows, split screens, title cards, slide/zoom/fade animations) using your own photos and videos.

## 📦 Installation

Install this skill into your AI coding agent (Claude Code, Cursor, Codex, Antigravity, etc.) using the [Skills CLI](https://skills.sh):

```bash
# Global installation (recommended)
npx skills add Harmish001/video-template-replicator -g

# Or install for a specific agent (e.g., claude-code, cursor)
npx skills add Harmish001/video-template-replicator --agent claude-code
```

### Manual Installation
You can also copy or clone this repository into your agent's skills folder:
- **Claude Code**: `.claude/skills/video-template-replicator` or `~/.claude/skills/video-template-replicator`
- **Antigravity / Generic Agents**: `.agents/skills/video-template-replicator`

---

## 🛠️ Prerequisites
- **Python 3.9+**
- **FFmpeg & FFprobe** installed and available on your system `PATH`
- Python packages:
  ```bash
  pip install pillow numpy
  ```

---

## 🚀 How It Works

1. **Folder Setup**:
   - `template/` - Place exactly one reference MP4/MOV video here.
   - `content/` - Place your photos, video clips, and optional background audio here.
2. **Execution**:
   Ask your AI agent:
   > *"Recreate the video template in `template/` using my media in `content/`"*
3. **Output**:
   The agent analyzes the template frame-by-frame, generates a specification, asks questions only when necessary, and renders your finished video and thumbnail to `video-result/`.

---

## 📂 Repository Structure

- `SKILL.md` — Core instructions, workflow, and metadata for the AI agent.
- `scripts/` — Deterministic Python scripts:
  - `analyze_template.py` — Extracts frame difference sheets and scene metrics.
  - `inventory_media.py` — Catalogs user media and suggests slot assignments.
  - `compare_sheets.py` — Visual side-by-side comparison for QA.
  - `render.py` — High-performance frame compositor and FFmpeg pipeline.
- `references/` — Playbooks and schema definitions (`spec-reference.md`, `analysis-playbook.md`).
- `assets/` — Reference templates and example specifications (`example-spec-grid-reveal.json`).

---

## 📄 License
MIT
