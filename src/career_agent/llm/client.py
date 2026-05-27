import httpx
from loguru import logger
import google.generativeai as genai
from career_agent.config import settings

def call_openrouter(prompt: str, system_instruction: str = None) -> str:
    """Call the primary model (DeepSeek-v4-flash) via OpenRouter."""
    if not settings.openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY is not set in the configuration.")
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Akuma21205/career-agent", # OpenRouter requires/recommends these headers
        "X-Title": "Career Agent"
    }
    
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    
    payload = {
        "model": settings.llm_model,
        "messages": messages
    }
    
    logger.info(f"Calling OpenRouter primary model: {settings.llm_model}")
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        resp_json = r.json()
        
        if "choices" in resp_json and len(resp_json["choices"]) > 0:
            return resp_json["choices"][0]["message"]["content"]
        else:
            raise RuntimeError(f"Unexpected response structure from OpenRouter: {resp_json}")

def call_gemini(prompt: str, system_instruction: str = None) -> str:
    """Call the fallback model (Gemini) using the google-generativeai SDK."""
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is not set in the configuration.")
        
    logger.info(f"Calling Gemini fallback model: {settings.fallback_llm_model}")
    genai.configure(api_key=settings.gemini_api_key)
    
    if system_instruction:
        model = genai.GenerativeModel(
            model_name=settings.fallback_llm_model,
            system_instruction=system_instruction
        )
    else:
        model = genai.GenerativeModel(model_name=settings.fallback_llm_model)
        
    response = model.generate_content(prompt)
    return response.text

def generate_text(prompt: str, system_instruction: str = None) -> str:
    """
    Generate text using the primary LLM (DeepSeek via OpenRouter),
    falling back to Gemini if the primary fails.
    """
    try:
        return call_openrouter(prompt, system_instruction)
    except Exception as e:
        logger.warning(f"Primary model ({settings.llm_model}) failed: {e}. Falling back to Gemini...")
        try:
            return call_gemini(prompt, system_instruction)
        except Exception as fallback_err:
            logger.critical(f"Both primary and fallback models failed! Fallback error: {fallback_err}")
            raise fallback_err
