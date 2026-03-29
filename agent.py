"""
Google ADK Agent — EduTrend AI Mentor.

Uses Gemini Flash 2.0 (free tier) to answer questions about:
- Trending tech skills
- Personalized learning roadmaps
- Career path guidance
- Certifications and project suggestions
"""
import os
import asyncio
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# System prompt for the EduTrend AI Mentor
SYSTEM_PROMPT = """You are EduTrend AI Mentor, an expert educational guide powered by real-time data.
You help students and professionals with:
1. **Trending tech skills** - What's hot in the job market right now
2. **Learning roadmaps** - Step-by-step structured paths to master skills
3. **Career guidance** - Compare careers, salary insights, growth potential
4. **Certifications** - Recommend the best certs for each skill path
5. **Projects** - Suggest hands-on projects to practice and build portfolio

When generating roadmaps, always use this JSON structure if asked for a roadmap:
{
  "type": "roadmap",
  "skill": "<skill name>",
  "duration": "<estimated time>",
  "difficulty": "<Beginner|Intermediate|Advanced>",
  "modules": [
    {
      "title": "<module title>",
      "topics": ["topic1", "topic2"],
      "resources": ["resource1", "resource2"],
      "project": "<mini project idea>",
      "duration_weeks": <number>
    }
  ],
  "certifications": ["cert1", "cert2"],
  "final_project": "<capstone project idea>"
}

When NOT generating a roadmap, respond in clear markdown with:
- Use **bold** for key terms
- Use numbered lists for steps
- Use 🔥 🚀 💡 📚 emojis sparingly for visual appeal
- Keep responses focused and actionable
- Always end with a follow-up question or suggestion

Current date context: March 2026. You have knowledge of the latest AI, ML, Web Dev, Cloud, and Cybersecurity trends.
"""


async def get_mentor_response(
    user_message: str,
    history: list[dict],
    user_id: int | None = None,
) -> str:
    """
    Call Gemini Flash with optional RAG context from ChromaDB.

    Args:
        user_message: The user's latest message
        history: List of {"role": "user"|"assistant", "content": "..."} dicts
        user_id: Optional user ID for personalized ChromaDB collection

    Returns:
        AI response text (may include JSON roadmap block)
    """
    # ── RAG retrieval ─────────────────────────────────────────────────────────
    rag_context = _retrieve_relevant_context(user_message, user_id)

    system = SYSTEM_PROMPT
    if rag_context:
        system = (
            SYSTEM_PROMPT
            + "\n\n## CONTEXT FROM USER'S UPLOADED DOCUMENTS\n"
            + rag_context
            + "\n\nWhen relevant, use the above document content to enrich your answers."
        )

    if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
        prefix = ""
        if rag_context:
            prefix = "📄 *Using context from your uploaded documents.*\n\n"
        return prefix + _fallback_response(user_message)

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Build conversation history for the new SDK
    contents: list[types.Content] = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(
            types.Content(role=role, parts=[types.Part(text=msg["content"])])
        )
    # Add the current user message
    contents.append(
        types.Content(role="user", parts=[types.Part(text=user_message)])
    )

    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=0.7,
    )

    # Run in thread executor so async FastAPI isn't blocked
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(
                model="gemini-flash-lite-latest",
                contents=contents,
                config=config,
            ),
        )
        return response.text
    except Exception as e:
        err_str = str(e)
        # Silently fall back on quota / rate-limit errors
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
            prefix = "📄 *Using context from your uploaded documents.*\n\n" if rag_context else ""
            return prefix + _fallback_response(user_message)
        raise  # re-raise other unexpected errors


def _retrieve_relevant_context(
    query: str, user_id: int | None = None, n_results: int = 3
) -> str:
    """
    Query ChromaDB for relevant chunks from the user's uploaded documents.
    Returns concatenated context string or empty string if nothing found.
    """
    try:
        import chromadb
        chroma_path = os.getenv("CHROMA_PATH", "./chroma_db")
        client = chromadb.PersistentClient(path=chroma_path)
        collection_name = f"user_{user_id}_docs" if user_id else "guest_docs"

        try:
            collection = client.get_collection(name=collection_name)
        except Exception:
            return ""  # Collection doesn't exist yet

        count = collection.count()
        if count == 0:
            return ""

        results = collection.query(
            query_texts=[query],
            n_results=min(n_results, count),
        )
        docs = results.get("documents", [[]])[0]
        if not docs:
            return ""

        return "\n\n---\n\n".join(docs)
    except Exception:
        return ""


def _fallback_response(user_message: str) -> str:
    """
    Return a helpful response when Gemini API key is not configured.
    Used for dev/demo mode.
    """
    lower = user_message.lower()

    if any(w in lower for w in ["roadmap", "path", "plan", "learn"]):
        return """🚀 **Here's a sample learning roadmap for AI/ML:**

**📅 Phase 1 — Foundations (4 weeks)**
- Python programming basics
- Mathematics: Linear Algebra, Calculus, Statistics
- 🎯 Project: Data analysis on a real dataset

**📅 Phase 2 — Core ML (6 weeks)**  
- Supervised & Unsupervised Learning
- Scikit-learn in depth
- 🎯 Project: Build a predictive model

**📅 Phase 3 — Deep Learning (6 weeks)**
- Neural Networks fundamentals
- PyTorch or TensorFlow
- 🎯 Project: Image classifier

**📅 Phase 4 — LLMs & AI Agents (4 weeks)**
- Transformer architecture
- LangChain / Google ADK
- RAG systems
- 🎯 Project: Build your own AI assistant

**🏆 Certifications:**
- Google ML Engineer certification
- DeepLearning.AI courses (Coursera)

> ⚠️ *To enable live AI responses, add your Gemini API key to backend/.env*

Want me to personalise this roadmap for a specific skill?"""

    if any(w in lower for w in ["trending", "top", "hot", "demand", "skill"]):
        return """🔥 **Top Trending Skills in 2026:**

1. **Generative AI & LLMs** — +340% growth | 98/100 demand score
2. **AI Agents** — +280% growth | autonomous system builders
3. **RAG Systems** — +210% growth | enterprise AI standard
4. **Prompt Engineering** — +195% growth | fast to learn, high ROI
5. **MLOps** — +165% growth | taking models to production

**💡 Emerging to Watch:**
- Multi-modal AI (text + image + audio)
- AI Safety & Alignment

> ⚠️ *Add your Gemini API key to backend/.env for live personalised answers*

Which skill would you like a learning roadmap for?"""

    return """👋 Hello! I'm your **EduTrend AI Mentor**.

I can help you with:
- 📈 **Trending skills** and career insights
- 📚 **Personalised learning roadmaps**  
- 🎯 **Career path comparisons**
- 🏆 **Certification recommendations**
- 💻 **Project ideas** for your portfolio

> ⚠️ *Note: To enable full AI-powered responses, add your free Gemini API key to `backend/.env`*
> Get it free at: https://aistudio.google.com

What would you like to explore today?"""
