from strands import Agent, tool
from strands.models import BedrockModel
from datetime import datetime
from typing import Optional


# =============================================================================
# TOOLS LAYER - Actions the agent can perform
# =============================================================================

@tool
def get_weather(city: str) -> dict:
    # Simulated weather data (in production, call a real API)
    weather_data = {
        "San Francisco": {"temp_f": 62, "conditions": "Partly Cloudy", "humidity": 75},
        "New York": {"temp_f": 78, "conditions": "Sunny", "humidity": 55},
        "Seattle": {"temp_f": 58, "conditions": "Rainy", "humidity": 85},
        "Austin": {"temp_f": 95, "conditions": "Hot and Sunny", "humidity": 40},
    }
    
    result = weather_data.get(city, {"temp_f": 70, "conditions": "Unknown", "humidity": 50})
    result["city"] = city
    result["timestamp"] = datetime.now().isoformat()
    
    return result


@tool
def calculate_travel_time(origin: str, destination: str, mode: str = "driving") -> dict:
    # Simulated travel calculations (in production, use Google Maps API, etc.)
    base_times = {
        ("San Francisco", "San Jose"): {"driving": 45, "transit": 75, "walking": 600},
        ("San Francisco", "Oakland"): {"driving": 25, "transit": 40, "walking": 180},
        ("New York", "Brooklyn"): {"driving": 30, "transit": 25, "walking": 120},
    }
    
    key = (origin, destination)
    reverse_key = (destination, origin)
    
    if key in base_times:
        times = base_times[key]
    elif reverse_key in base_times:
        times = base_times[reverse_key]
    else:
        # Default estimate
        times = {"driving": 30, "transit": 45, "walking": 90}
    
    duration_minutes = times.get(mode, times["driving"])
    
    return {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "duration_minutes": duration_minutes,
        "duration_text": f"{duration_minutes} minutes" if duration_minutes < 60 else f"{duration_minutes // 60}h {duration_minutes % 60}m"
    }


@tool
def get_local_events(city: str, category: Optional[str] = None) -> list:
    # Simulated events data
    all_events = {
        "San Francisco": [
            {"name": "Jazz Night at SFJAZZ", "category": "music", "time": "8:00 PM", "venue": "SFJAZZ Center"},
            {"name": "Giants vs Dodgers", "category": "sports", "time": "7:15 PM", "venue": "Oracle Park"},
            {"name": "Street Food Festival", "category": "food", "time": "11:00 AM", "venue": "Ferry Building"},
            {"name": "AI Meetup", "category": "tech", "time": "6:30 PM", "venue": "Moscone Center"},
        ],
        "New York": [
            {"name": "Broadway Show: Hamilton", "category": "music", "time": "7:30 PM", "venue": "Richard Rodgers Theatre"},
            {"name": "Yankees Game", "category": "sports", "time": "7:05 PM", "venue": "Yankee Stadium"},
            {"name": "Smorgasburg Food Market", "category": "food", "time": "11:00 AM", "venue": "Williamsburg"},
        ],
    }
    
    events = all_events.get(city, [])
    
    if category:
        events = [e for e in events if e["category"] == category.lower()]
    
    return events


# =============================================================================
# ORCHESTRATION LAYER - Strands Agent Configuration
# =============================================================================

def create_agent() -> Agent:

    # Configure the reasoning layer (Amazon Bedrock with Claude)
    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        region_name="us-west-2"
    )
    
    # System prompt guides the reasoning layer
    system_prompt = """You are a helpful local guide assistant. You help users plan 
their day by providing weather information, travel times, and local events.

When a user asks about planning activities:
1. First check the weather to ensure conditions are suitable
2. Look up relevant local events based on their interests
3. Calculate travel times if they need to get somewhere
4. Synthesize all information into a helpful recommendation

Be concise and practical in your responses."""
    
    # Create the agent - this is the orchestration layer
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[get_weather, calculate_travel_time, get_local_events]
    )
    
    return agent


def main():
    # Create the agent
    agent = create_agent()
    
    # Example query that will trigger multiple tool calls
    user_query = """
    I'm in San Francisco and want to do something fun this evening. 
    What's the weather like, and are there any music events happening? 
    If there's something good, how long would it take me to get there from the Ferry Building?
    """
    
    # Run the agent - this triggers the full three-layer flow
    response = agent(user_query)
    
    print(response)


if __name__ == "__main__":
    main()
