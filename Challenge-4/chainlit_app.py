#!/usr/bin/env python3
"""
Gemini Chainlit Search Assistant
================================
A lightweight Chainlit chatbot that utilizes the Gemini 1.5 Flash model 
with integrated Google Search capabilities via Vertex AI.
"""

import os
import sys
import logging
import chainlit as cl

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: 'google-genai' SDK not found. Install it via: pip install google-genai", file=sys.stderr)
    sys.exit(1)

# Configuration
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL_ID = "gemini-2.5-flash"  # Using version-qualified ID for Vertex AI reliability

# Setup logging
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("ChainlitSearchBot")

@cl.on_chat_start
async def start():
    """Initializes the GenAI client and sets up the chat session."""
    if not PROJECT_ID:
        await cl.Message(
            content="Error: GOOGLE_CLOUD_PROJECT environment variable is not set. Please set it to your GCP project ID.",
            author="System"
        ).send()
        return

    try:
        # Initialize Client using Vertex AI and ADC
        client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=LOCATION
        )

        # Enable the Google Search tool
        search_tool = types.Tool(
            google_search=types.GoogleSearch()
        )

        # Start a chat session with the search tool enabled
        # Using the async (.aio) client for better performance in Chainlit
        chat = client.aio.chats.create(
            model=MODEL_ID,
            config=types.GenerateContentConfig(
                tools=[search_tool],
                system_instruction="You are a helpful assistant with real-time internet access. Use Google Search to verify facts."
            )
        )
        
        # Store both the client and the chat to keep the connection alive
        cl.user_session.set("genai_client", client)
        cl.user_session.set("chat_session", chat)

        await cl.Message(
            content=f"--- 🤖 Gemini Search Bot ({MODEL_ID}) --- \nHello! I'm your AI assistant with internet access. How can I help you today?",
            author="Gemini"
        ).send()

    except Exception as e:
        logger.error(f"Chat initialization fault: {e}")
        await cl.Message(
            content=f"A critical error occurred during initialization: {e}. Please check your configuration and try again.",
            author="System"
        ).send()

@cl.on_message
async def main(message: cl.Message):
    """Handles user messages and sends them to the Gemini chat session."""
    chat = cl.user_session.get("chat_session")

    if not chat:
        await cl.Message(
            content="Chat session not initialized. Please restart the chat.",
            author="System"
        ).send()
        return

    response_message = cl.Message(content="", author="Gemini")
    await response_message.send()

    try:
        # Send message and stream the response
        response_stream = await chat.send_message_stream(message.content)
        
        full_response_content = ""
        async for chunk in response_stream:
            if chunk.text:
                full_response_content += chunk.text
                await response_message.stream_token(chunk.text)
        
        # Update the message content after streaming is complete
        response_message.content = full_response_content
        await response_message.update()

    except Exception as e:
        logger.error(f"Error during message processing: {e}")
        await cl.Message(
            content=f"An error occurred while processing your message: {e}",
            author="System"
        ).send()