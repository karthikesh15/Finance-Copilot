import os
import json
from groq import Groq

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# 1. Text Parser using groq/compound-mini
def parse_transaction_text(text_input):
    prompt = f"""
    Extract financial transaction details from this message into strict JSON:
    "{text_input}"

    Expected JSON format:
    {{
        "type": "inflow" or "outflow",
        "amount": 0.0,
        "category": "string",
        "vendor_customer": "string"
    }}
    Respond ONLY with raw valid JSON object, no markdown formatting or commentary.
    """
    response = groq_client.chat.completions.create(
        model="groq/compound-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

# 2. Receipt OCR Parser using groq/compound-mini
def parse_receipt_image(image_base64):
    prompt = "Extract transaction details from this receipt image: type ('inflow'/'outflow'), amount (float), category, vendor_customer. Respond ONLY in strict valid JSON."
    response = groq_client.chat.completions.create(
        model="groq/compound-mini",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
        }],
        temperature=0.1,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

# 3. Voice Note Transcription using whisper-large-v3-turbo
def transcribe_voice_note(file_bytes):
    temp_path = "temp_voice.ogg"
    with open(temp_path, "wb") as f:
        f.write(file_bytes)
    
    with open(temp_path, "rb") as audio_file:
        transcription = groq_client.audio.transcriptions.create(
            file=(temp_path, audio_file.read()),
            model="whisper-large-v3-turbo"
        )
    
    if os.path.exists(temp_path):
        os.remove(temp_path)
        
    return transcription.text
