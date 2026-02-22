#!/usr/bin/env python3
"""End-to-end test: provider-openclaw through the sidecar.

Starts sidecar on a Unix socket, creates a session that uses provider-openclaw
(which routes LLM calls back through openclaw/llm_complete), and verifies
the full round-trip works.
"""

import asyncio
import json
import os
import sys
import tempfile

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def main():
    socket_path = tempfile.mktemp(suffix=".sock")
    
    print(f"Starting sidecar on {socket_path}...")
    
    # Start sidecar as subprocess using uv run
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "amplifier-openclaw", "serve",
        "--socket", socket_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.path.join(os.path.dirname(__file__), ".."),
    )
    
    # Wait for ready
    await asyncio.sleep(3)
    
    try:
        # Connect
        print("Connecting to sidecar...")
        reader, writer = await asyncio.open_unix_connection(socket_path)
        
        # Read bridge/ready
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        ready = json.loads(line)
        print(f"Ready: {ready}")
        assert ready.get("method") == "bridge/ready", f"Expected bridge/ready, got {ready}"
        
        # Create session with foundation bundle (uses provider-anthropic by default)
        print("\nCreating session...")
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/create",
            "params": {"bundle": "foundation", "cwd": "."},
        }
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        
        # Read response (skip notifications)
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=60)
            msg = json.loads(line)
            if msg.get("id") == 1:
                break
            print(f"  notification: {msg.get('method', 'unknown')}")
        
        if "error" in msg:
            print(f"ERROR creating session: {msg['error']}")
            return
        
        session_id = msg["result"]["session_id"]
        tools = msg["result"].get("tools", [])
        print(f"Session created: {session_id}")
        print(f"Tools available: {len(tools)}")
        
        # Execute a simple prompt
        print("\nExecuting prompt: 'What is 2+2? Answer with just the number.'")
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/execute",
            "params": {
                "session_id": session_id,
                "prompt": "What is 2+2? Answer with just the number.",
                "timeout": 60,
            },
        }
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        
        # Read response
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=60)
            msg = json.loads(line)
            if msg.get("id") == 2:
                break
            print(f"  notification: {msg.get('method', 'unknown')}")
        
        if "error" in msg:
            print(f"ERROR executing: {msg['error']}")
        else:
            result = msg["result"]
            print(f"\nResponse: {result.get('response', 'N/A')}")
            print(f"Usage: {result.get('usage', {})}")
            print(f"Status: {result.get('status', 'N/A')}")
        
        # Now test openclaw/llm_complete directly
        print("\n--- Testing openclaw/llm_complete directly ---")
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "openclaw/llm_complete",
            "params": {
                "messages": [{"role": "user", "content": "Say just 'hello'"}],
                "max_tokens": 50,
            },
        }
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            msg = json.loads(line)
            if msg.get("id") == 3:
                break
        
        if "error" in msg:
            print(f"ERROR: {msg['error']}")
        else:
            result = msg["result"]
            print(f"LLM Response text: {result.get('text', 'N/A')}")
            print(f"Usage: {result.get('usage', {})}")
            print(f"Model: {result.get('model', 'N/A')}")
        
        # Cleanup
        print("\nCleaning up session...")
        request = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "session/cleanup",
            "params": {"session_id": session_id},
        }
        writer.write((json.dumps(request) + "\n").encode())
        await writer.drain()
        
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        msg = json.loads(line)
        print(f"Cleanup: {'OK' if msg.get('result') else msg.get('error')}")
        
        writer.close()
        
    finally:
        try:
            proc.terminate()
            await proc.wait()
        except (ProcessLookupError, OSError):
            pass
        # Clean up socket
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
    
    print("\n✅ E2E test complete!")


if __name__ == "__main__":
    asyncio.run(main())
