# Transpile Claude Code config into OpenCode's and Pi's schemas.
#
# The Claude-format files under agents/, skills/, and mcp.json are the source
# of truth; the OpenCode and Pi variants are derived (never hand-edited).
# OpenCode hooks are out of scope (TypeScript plugins, not shell scripts wired
# via settings.json); Pi's hooks bridge IS wired (pi/hooks-bridge.json +
# pi/extensions/claude-hooks-bridge.ts) since Pi's `pi.on(event, handler)`
# extension API maps cleanly onto Claude's PreToolUse/Stop hook scripts.

AI_DIR       := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
GEN_AGENTS   := $(AI_DIR)opencode/agents
GEN_PLUGINS  := $(AI_DIR)opencode/plugins
OC_DIR       := $(HOME)/.config/opencode
OC_AGENTS    := $(OC_DIR)/agents
OC_PLUGINS   := $(OC_DIR)/plugins
PI_GEN_AGENTS := $(AI_DIR)pi/agents
PI_DIR        := $(HOME)/.pi/agent
PI_AGENTS     := $(PI_DIR)/agents
PI_SKILLS     := $(PI_DIR)/skills
PI_EXTENSIONS := $(PI_DIR)/extensions
PY           := python3

.DEFAULT_GOAL := help
.PHONY: help opencode opencode-agents opencode-mcp opencode-link opencode-hooks opencode-check opencode-clean \
        pi pi-agents pi-mcp pi-skills-link pi-extensions-link pi-check pi-clean \
        install-coord test-coord

help: ## Show this help
	@echo "OpenCode transpile targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

opencode: opencode-agents opencode-mcp opencode-link ## Transpile agents + MCP and wire the symlink

opencode-agents: ## Transpile agents/*.md -> opencode/agents/*.md
	@$(PY) $(AI_DIR)opencode/transpile_agents.py

opencode-mcp: ## Merge mcp.json into the chezmoi-managed opencode.json (run on work; commit via chezmoi)
	@src="$$(chezmoi source-path "$(HOME)/.dotfiles/config/opencode/opencode.json")"; \
	 OPENCODE_CONFIG="$$src" $(PY) $(AI_DIR)opencode/transpile_mcp.py; \
	 chezmoi apply "$(HOME)/.dotfiles/config/opencode/opencode.json"; \
	 echo "note: opencode.json is chezmoi-managed -- commit the source change"

opencode-link: ## Point ~/.config/opencode/agents at the generated dir
	@if [ -d "$(OC_AGENTS)" ] && [ ! -L "$(OC_AGENTS)" ]; then \
		echo "WARNING: $(OC_AGENTS) is a real directory -- not relinking"; \
	else \
		ln -sfn "$(GEN_AGENTS)" "$(OC_AGENTS)"; \
		echo "linked $(OC_AGENTS) -> $(GEN_AGENTS)"; \
	fi

opencode-hooks: ## Install the Claude->OpenCode hooks bridge plugin (opt-in)
	@if [ -d "$(OC_PLUGINS)" ] && [ ! -L "$(OC_PLUGINS)" ]; then \
		echo "WARNING: $(OC_PLUGINS) is a real directory -- not relinking"; \
	else \
		ln -sfn "$(GEN_PLUGINS)" "$(OC_PLUGINS)"; \
		echo "linked $(OC_PLUGINS) -> $(GEN_PLUGINS)"; \
	fi

opencode-check: ## Fail if generated agents are stale vs source (CI/pre-commit guard)
	@$(PY) $(AI_DIR)opencode/transpile_agents.py --check

opencode-clean: ## Remove generated agent files
	@rm -rf "$(GEN_AGENTS)"
	@echo "removed $(GEN_AGENTS)"

pi: pi-agents pi-mcp pi-skills-link pi-extensions-link ## Transpile agents + MCP and wire skills/extensions symlinks

pi-agents: ## Transpile agents/*.md -> pi/agents/*.md, then symlink ~/.pi/agent/agents to it
	@$(PY) $(AI_DIR)pi/transpile_agents.py
	@if [ -d "$(PI_AGENTS)" ] && [ ! -L "$(PI_AGENTS)" ]; then \
		echo "WARNING: $(PI_AGENTS) is a real directory -- not relinking"; \
	else \
		ln -sfn "$(PI_GEN_AGENTS)" "$(PI_AGENTS)"; \
		echo "linked $(PI_AGENTS) -> $(PI_GEN_AGENTS)"; \
	fi

pi-mcp: ## Merge mcp.json into the chezmoi-managed ~/.pi/agent/mcp.json (first run: chezmoi add ~/.pi/agent/mcp.json)
	@src="$$(chezmoi source-path "$(PI_DIR)/mcp.json")" || { \
		echo "~/.pi/agent/mcp.json is not chezmoi-managed yet -- run once:"; \
		echo "  $(PY) $(AI_DIR)pi/transpile_mcp.py && chezmoi add $(PI_DIR)/mcp.json"; \
		exit 1; \
	}; \
	 PI_MCP_CONFIG="$$src" $(PY) $(AI_DIR)pi/transpile_mcp.py; \
	 chezmoi apply "$(PI_DIR)/mcp.json"; \
	 echo "note: $(PI_DIR)/mcp.json is chezmoi-managed -- commit the source change"

pi-skills-link: ## Point ~/.pi/agent/skills at the shared skills/ directory
	@if [ -d "$(PI_SKILLS)" ] && [ ! -L "$(PI_SKILLS)" ]; then \
		echo "WARNING: $(PI_SKILLS) is a real directory -- not relinking"; \
	else \
		ln -sfn "$(AI_DIR)skills" "$(PI_SKILLS)"; \
		echo "linked $(PI_SKILLS) -> $(AI_DIR)skills"; \
	fi

pi-extensions-link: ## Symlink the hooks-bridge config and extension files into ~/.pi/agent
	@mkdir -p "$(PI_EXTENSIONS)"
	@ln -sfn "$(AI_DIR)pi/hooks-bridge.json" "$(PI_DIR)/hooks-bridge.json"
	@ln -sfn "$(AI_DIR)pi/extensions/claude-hooks-bridge.ts" "$(PI_EXTENSIONS)/claude-hooks-bridge.ts"
	@ln -sfn "$(AI_DIR)pi/extensions/herdr-pane-subagent.ts" "$(PI_EXTENSIONS)/herdr-pane-subagent.ts"
	@ln -sfn "$(AI_DIR)pi/extensions/memory-track.ts" "$(PI_EXTENSIONS)/memory-track.ts"
	@echo "linked hooks-bridge.json + extensions -> $(PI_DIR)"

pi-check: ## Fail if generated pi agents are stale vs source (CI/pre-commit guard)
	@$(PY) $(AI_DIR)pi/transpile_agents.py --check

pi-clean: ## Remove generated pi agent files
	@rm -rf "$(PI_GEN_AGENTS)"
	@echo "removed $(PI_GEN_AGENTS)"

# --- coordinate plugin -----------------------------------------------------
COORD_CLI := $(AI_DIR)plugins/coordinate/scripts/coord.py
LOCAL_BIN := $(HOME)/.local/bin

install-coord: ## Install the `coord` CLI shim into ~/.local/bin (must be on PATH)
	@mkdir -p "$(LOCAL_BIN)"
	@printf '#!/usr/bin/env bash\nset -euo pipefail\nexec python3 "%s" "$$@"\n' "$(COORD_CLI)" > "$(LOCAL_BIN)/coord"
	@chmod +x "$(LOCAL_BIN)/coord"
	@echo "installed coord -> $(LOCAL_BIN)/coord (targets $(COORD_CLI))"

test-coord: ## Run the coord CLI test suite (stdlib unittest, no deps)
	@cd "$(AI_DIR)plugins/coordinate/scripts" && $(PY) -m unittest tests.test_coord -v
