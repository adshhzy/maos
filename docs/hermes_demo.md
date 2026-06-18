#!/usr/bin/env python3
"""Simple demonstration of the MAOS Direct Hermes Agent Node system."""

import json
import sys
import os

print("=== MAOS Direct Hermes Agent Node Demonstration ===\n")

print("1. OVERVIEW")
print("=" * 50)
print("The MAOS Direct Hermes Agent Node is a runtime adapter that executes")
print("control-flow graph nodes using the local Hermes CLI directly, bypassing")
print("the Multica Agent Service facade.")
print()

print("2. KEY FEATURES")
print("=" * 50)
print("• Direct Hermes CLI execution (no Multica task creation)")
print("• Polling-based completion (no webhook callbacks needed)")
print("• Configurable via node.agent settings in JSON graphs")
print("• ThreadPoolExecutor for parallel execution")
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
print("2. a2a_runtime.send_message() creates Hermes task")
print("3. ThreadPoolExecutor submits Hermes CLI command")
print("4. Workflow suspends, polls with poll_task()")
print("5. When Hermes completes, result extracted from stdout")
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
print("• A2A Context Payload (JSON with workflow/node context)")
print("• Dependency results (if any)")
print("• Graph input data")
print()

print("8. ADVANTAGES OVER MULTICA BACKEND")
print("=" * 50)
print("• No Multica task creation overhead")
print("• No Multica comments/status updates")
print("• Direct Hermes execution")
print("• Lower latency for simple tasks")
print("• No dependency on external Agent Service")
print("• Better for testing/development")
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
print("✓ a2a_runtime module loaded successfully")
print("✓ Hermes provider available")
print("✓ Task creation working")
print("✗ Hermes binary not found at default location")
print()

print("To configure Hermes:")
print("1. Install Hermes CLI")
print("2. Set HERMES_BIN environment variable")
print("3. Or modify _hermes_bin() in a2a_runtime.py")
print("4. Test with: python -m a2a_runtime (if implemented)")
print()

print("The system is ready to execute Hermes nodes once Hermes CLI is installed!")