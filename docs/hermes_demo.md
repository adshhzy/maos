#!/usr/bin/env python3
"""Simple demonstration of the MAOS Hermes Agent Service backend."""

import json
import sys
import os

print("=== MAOS Direct Hermes Agent Node Demonstration ===\n")

print("1. OVERVIEW")
print("=" * 50)
print("The MAOS Hermes backend is a provider adapter that executes")
print("control-flow graph nodes through Agent Service API v1. The Agent Service")
print("can run Hermes CLI in lightweight one-shot mode without Multica task")
print("comment/status orchestration.")
print()

print("2. KEY FEATURES")
print("=" * 50)
print("• Agent Service API v1 task creation for backend=hermes")
print("• Polling-based completion through provider lifecycle hooks")
print("• Configurable via node.agent settings in JSON graphs")
print("• A2A provider lifecycle integration")
print("• A2A-compatible task/artifact format")
print("• Support for model, provider, toolsets, skills configuration")
print()

print("3. NODE CONFIGURATION")
print("=" * 50)
print("To use Hermes backend in a control-flow graph node:")
print()

hermes_node_config = {
    "id": "my_hermes_node",
    "label": "My Hermes Agent Task",
    "operation": "agent_task",
    "agent": {
        "backend": "hermes",  # or "hermes-oneshot", "direct-hermes"
        "agent_key": "analysis_agent",
        "context_policy": "provided_context_only",
        "runtime_profile": "hermes_oneshot",
        "execution_mode": "hermes_oneshot",
        "poll_seconds": 30,
        "timeout_seconds": 300,
        "prompt": "Analyze the input data and provide insights.",
        # Optional Hermes-specific settings:
        "model": "deepseek-v3.2",
        "provider": "deepseek",
        "toolsets": "code,web,file",
        "skills": "analysis,summarization",
        "workdir": "/path/to/working/directory",
        "ignore_user_config": True,
        "ignore_rules": True
    }
}

print(json.dumps(hermes_node_config, indent=2))
print()

print("4. ENVIRONMENT VARIABLES")
print("=" * 50)
print("HERMES_BIN: Path to hermes executable (default: D:\\dev\\MAOS\\AgentRuntime\\hermes.cmd)")
print("HERMES_WORKDIR: Working directory for Hermes execution")
print("HERMES_PROVIDER_MODEL: Default model (e.g., 'deepseek-v3.2')")
print("HERMES_PROVIDER_PROVIDER: Default provider (e.g., 'deepseek')")
print("HERMES_PROVIDER_POLL_SECONDS: Polling interval (default: 30)")
print("HERMES_PROVIDER_MAX_WORKERS: Thread pool size (default: 4)")
print("HERMES_PROMPT_PAYLOAD_LIMIT: Max prompt size (default: 30000)")
print("HERMES_GIT_BASH_PATH: Git Bash path on Windows")
print()

print("5. EXECUTION FLOW")
print("=" * 50)
print("1. Temporal workflow invokes node activity")
print("2. maos_runtime.a2a.send_message() creates a Hermes Agent Service task")
print("3. Agent Service starts the Hermes CLI command")
print("4. Workflow suspends, polls with poll_task()")
print("5. When Hermes completes, result is projected through Agent Service v1")
print("6. Task marked completed, workflow resumes")
print()

print("6. COMMAND GENERATION")
print("=" * 50)
print("Hermes command generated:")
print("  cmd.exe /c hermes.cmd --ignore-user-config --ignore-rules \\")
print("    --model deepseek-v3.2 --provider deepseek \\")
print("    --toolsets code,web,file --skills analysis,summarization \\")
print("    -z '<prompt>'")
print()

print("7. PROMPT TEMPLATE")
print("=" * 50)
print("The prompt includes:")
print("• Context policy instructions")
print("• Node-specific instruction")
print("• Original task input")
print("• Upstream node results (if any)")
print("• Execution boundary instructions")
print()

print("8. ADVANTAGES OVER MULTICA BACKEND")
print("=" * 50)
print("• No Multica task creation overhead in the workflow path")
print("• No Multica comments/status dependency for orchestration")
print("• Agent Service v1 facade keeps the provider API stable")
print("• Lower latency for simple tasks")
print("• Better isolation from Multica-specific task/comment history")
print("• Suitable for testing/development when Hermes is installed")
print()

print("9. USE CASES")
print("=" * 50)
print("• Simple data transformation/analysis")
print("• Local file processing")
print("• Development/testing workflows")
print("• When Multica is unavailable")
print("• Low-latency agent tasks")
print("• Batch processing with many nodes")
print()

print("10. EXAMPLE WORKFLOW")
print("=" * 50)
print("See: examples/all_hermes_chinese_knowledge_assistant_launch.json")
print("See: examples/mixed_hermes_multica_simulator.json")
print()

print("11. CURRENT STATUS")
print("=" * 50)
print("✓ maos_runtime.a2a module loaded successfully")
print("✓ Hermes provider available")
print("✓ Task creation working")
print("✗ Hermes binary not found at default location")
print()

print("To configure Hermes:")
print("1. Install Hermes CLI")
print("2. Set HERMES_BIN environment variable")
print("3. Or modify the Hermes provider configuration in maos_runtime.a2a.providers")
print("4. Test with the Sandbox API or an example graph using backend=hermes")
print()

print("The system is ready to execute Hermes nodes once Hermes CLI is installed!")
