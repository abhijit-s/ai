# Transpile Claude Code config into OpenCode's schemas.
#
# The Claude-format files under agents/ and mcp.json are the source of truth;
# the OpenCode variants are derived (never hand-edited). Hooks are deliberately
# out of scope for now -- OpenCode hooks are TypeScript plugins, not shell
# scripts wired via settings.json, so a bridge is a separate follow-up.

AI_DIR      := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
GEN_AGENTS  := $(AI_DIR)opencode/agents
GEN_PLUGINS := $(AI_DIR)opencode/plugins
OC_DIR      := $(HOME)/.config/opencode
OC_AGENTS   := $(OC_DIR)/agents
OC_PLUGINS  := $(OC_DIR)/plugins
PY          := python3

.DEFAULT_GOAL := help
.PHONY: help opencode opencode-agents opencode-mcp opencode-link opencode-hooks opencode-check opencode-clean

help: ## Show this help
	@echo "OpenCode transpile targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

opencode: opencode-agents opencode-mcp opencode-link ## Transpile agents + MCP and wire the symlink

opencode-agents: ## Transpile agents/*.md -> opencode/agents/*.md
	@$(PY) $(AI_DIR)opencode/transpile_agents.py

opencode-mcp: ## Merge mcp.json into ~/.config/opencode/opencode.json
	@$(PY) $(AI_DIR)opencode/transpile_mcp.py

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
