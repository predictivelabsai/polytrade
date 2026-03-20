#!/usr/bin/env python3
"""Test script for XAI API integration."""
import os
import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from model.llm import LLMProvider
from agent.agent import Agent
from agent.types import AgentConfig


async def test_xai_connection():
    """Test basic XAI API connection."""
    print("[*] Testing XAI API Connection...")
    
    # Load environment variables
    load_dotenv()
    
    xai_key = os.getenv("XAI_API_KEY")
    if not xai_key:
        print("[!] XAI_API_KEY not found in environment")
        return False
    
    print(f"[+] XAI API Key found: {xai_key[:20]}...")
    
    try:
        # Test LLM initialization
        print("[*] Initializing XAI LLM...")
        llm = LLMProvider.get_model(
            model="grok-3",
            provider="xai",
            temperature=0.7
        )
        print("[+] XAI LLM initialized successfully")
        
        # Test simple prompt
        print("[*] Testing simple prompt...")
        from langchain_core.messages import HumanMessage
        
        response = llm.invoke([
            HumanMessage(content="What is 2+2? Answer in one sentence.")
        ])
        
        print(f"[+] Response: {response.content}")
        return True
        
    except Exception as e:
        print(f"[!] Error: {str(e)}")
        return False


async def test_agent_basic():
    """Test basic agent functionality."""
    print("\n[*] Testing Agent Initialization...")
    
    load_dotenv()
    
    try:
        config = AgentConfig(
            model="grok-3",
            model_provider="xai",
            max_iterations=2
        )
        
        agent = Agent.create(config)
        print("[+] Agent created successfully")
        
        # Test with a simple query
        query = "What is the current price of Apple stock?"
        print(f"\n[*] Testing query: {query}")
        
        events = []
        async for event in agent.run(query):
            event_dict = {
                "type": type(event).__name__,
                "timestamp": datetime.now().isoformat()
            }
            
            if hasattr(event, "tool"):
                event_dict["tool"] = event.tool
            if hasattr(event, "answer"):
                event_dict["answer"] = event.answer[:100] if event.answer else None
            if hasattr(event, "chunk"):
                event_dict["chunk"] = event.chunk
            
            events.append(event_dict)
            print(f"[+] Event: {event_dict['type']}")
        
        return events
        
    except Exception as e:
        print(f"[!] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    """Run all tests."""
    print("=" * 60)
    print("PolyTrade - XAI Integration Test Suite")
    print("=" * 60)
    
    # Test 1: XAI Connection
    xai_ok = await test_xai_connection()
    
    # Test 2: Agent Basic
    if xai_ok:
        events = await test_agent_basic()
        
        # Save results
        results = {
            "timestamp": datetime.now().isoformat(),
            "tests": {
                "xai_connection": xai_ok,
                "agent_basic": events is not None
            },
            "events": events
        }
        
        # Create test-results directory if it doesn't exist
        os.makedirs("test-results", exist_ok=True)
        
        # Save results to JSON
        results_file = "test-results/xai_integration_test.json"
        with open(results_file, "w") as f:
            json.dump(results, f, indent=2)
        
        print(f"\n[+] Results saved to {results_file}")
    
    print("\n" + "=" * 60)
    print("Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
