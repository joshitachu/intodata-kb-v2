from __future__ import annotations

import tiktoken
from src.generation.intent_classifier import Intent                                          
from src.schemas import Source

MAX_CONTEXT_TOKENS = 3000
ENCODING_NAME = "cl100k_base"

# Original prompt (truncated in your snippet)
SYSTEM_PROMPT = (
    """You are a helpful knowledge-base assistant for a company. Answer the user's question based ONLY on the provided context. If the context does not contain enough information, say so clearly.

Always cite your sources using the [Source: filename, chunk N] format, but only if you use that information in your answer. Citations are only required when the information provided comes from the knowledge base; do not cite sources for basic questions that are not related to documents within the knowledge base
Example Response
User: What is the reason to live?

Assistant: I’m sorry, but the provided context does not contain information regarding the philosophical reason to live. My knowledge base is limited to company documentation.
"""
)

GREETING_PROMPT = (                                                                         
    "You are a friendly knowledge-base assistant for a company. "                               
    "The user is greeting you. Respond warmly and briefly, then offer to help "               
    "them find information in the knowledge base. Keep it to 1-2 sentences."                
)                                                                                           

THANKS_PROMPT = (                                                                             
    "You are a friendly knowledge-base assistant for a company. "                               
    "The user is thanking you. Acknowledge warmly and offer to help with "                    
    "anything else. Keep it to 1-2 sentences."                                              
)                                                                                           

GOODBYE_PROMPT = (                                                                          
    "You are a friendly knowledge-base assistant for a company. "                               
    "The user is saying goodbye. Give a warm sign-off and let them know "                     
    "they can come back anytime. Keep it to 1-2 sentences."                                 
)                                                                                           

NO_RESULTS_PROMPT = (                                                                       
    "You are a helpful knowledge-base assistant for a company. "                                
    "The user asked a question but no relevant documents were found in the knowledge base. "  
    "If the query is vague or unclear, ask a clarifying question to help narrow it down. "    
    "If the query seems reasonable but nothing was found, acknowledge that the information "  
    "isn't in the knowledge base and suggest they try rephrasing or ask about a related topic. "
    "NEVER make up information. Keep your response concise (2-3 sentences max)."            
)                                                                                           

_INTENT_TO_PROMPT: dict[Intent, str] = {                                                    
    Intent.GREETING: GREETING_PROMPT,                                                         
    Intent.THANKS: THANKS_PROMPT,                                                             
    Intent.GOODBYE: GOODBYE_PROMPT,                                                           
}                                                                                           


def build_messages(
    query: str,
    sources: list[Source],
) -> list[dict[str, str]]:
    """Build the chat messages list: system prompt + context + user query."""
    context = _build_context(sources)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
    ]



def _build_context(sources: list[Source]) -> str:
    """Concatenate source texts, truncating to stay within MAX_CONTEXT_TOKENS."""
    enc = tiktoken.get_encoding(ENCODING_NAME)
    parts: list[str] = []
    total_tokens = 0

    for src in sources:
        header = f"[Source: {src.filename}, chunk {src.chunk_index}]"
        block = f"{header}\n{src.text}\n"
        block_tokens = len(enc.encode(block))

        if total_tokens + block_tokens > MAX_CONTEXT_TOKENS:
            break
        parts.append(block)
        total_tokens += block_tokens

    return "\n".join(parts)


def build_chitchat_messages(                                                                
    query: str,                                                                             
    intent: Intent,                                                                         
    history: list[dict[str, str]] | None = None,                                            
) -> list[dict[str, str]]:                                                                  
    """Build LLM messages for non-search intents (greeting, thanks, goodbye)."""            
    system_prompt = _INTENT_TO_PROMPT[intent]                                               
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]         
                                                                                            
    if history:                                                                             
        messages.extend(history)                                                            
                                                                                            
    messages.append({"role": "user", "content": query})                                     
    return messages                                                                         


def build_no_results_messages(                                                              
    query: str,                                                                             
    history: list[dict[str, str]] | None = None,                                            
) -> list[dict[str, str]]:                                                                  
    """Build LLM messages for when search returns no results."""                           
    messages: list[dict[str, str]] = [{"role": "system", "content": NO_RESULTS_PROMPT}]     
                                                                                            
    if history:                                                                             
        messages.extend(history)                                                            
                                                                                            
    messages.append({"role": "user", "content": query})                                     
    return messages