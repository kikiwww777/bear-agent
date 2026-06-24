import argparse
from backend.agents.loop import create_agent, chat_loop

def run_cli():
    """启动 CLI 交互模式。"""
    agent = create_agent()
    chat_loop(agent)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bear Agent")
    args = parser.parse_args()
    run_cli()