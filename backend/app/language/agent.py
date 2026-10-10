"""The Writing Assistant: drafts customer messages natively in each writing language
(decisions L5 A, L7 A). It only drafts; sending goes through app/channels/send.py
like every other message, and the text is marked as AI-written (R7)."""

from app.agents.registry import L1, AgentDef, register_agent

AGENT_KEY = "writer"

register_agent(AgentDef(
    key=AGENT_KEY,
    name="Writing Assistant",
    purpose="Drafts messages to customers in English, Hindi and Telugu, written natively in "
            "that language (not translated from English), following the company's tone "
            "guide and glossary. It checks its own draft and rewrites it once if it breaks a "
            "rule. It never sends anything: a person, or an agent through the channel "
            "checks, does.",
    tools=(),
    default_level=L1, max_level=L1,
    triggers=("\"Draft it for me\" on the New message screen", "agents writing to customers"),
    model_tier="writing",
))
