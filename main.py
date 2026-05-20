import re
import json
import httpx
from datetime import datetime
from pydantic_settings import BaseSettings

from google import genai
from google.genai import types
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware

import firebase_admin
from fastapi import FastAPI
from firebase_admin import credentials, firestore
from repository import User, Response, GECRequest, SignInRequest, SignUpRequest, SaveRequest, SavedRequest, HistoryRequest

firebase_admin.initialize_app()
db = firestore.client(database_id='gec-tagging-db')

class Settings(BaseSettings):
  GEMINI_API_KEY: str
  GECTOR_URL: str

  class Config:
    # Tells Pydantic to read from your local .env file
    env_file = ".env"

app = FastAPI()
settings = Settings()
app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)

def smart_tokenize(text):
    # 1. Protect decimals/numbers, but space out other punctuation.
    # Group 1 (\d+[.,]\d+) matches things like 3.14 or 1,000.
    # Group 2 ([.,!?]) matches standard punctuation.
    text = re.sub(r'(\d+[.,]\d+)|([.,!?])', 
                  lambda m: m.group(1) if m.group(1) else f" {m.group(2)}", 
                  text)
    
    # 2. Add a space before common English contractions
    text = re.sub(r"('m|'s|'re|'ve|'ll|'d)\b", r" \1", text, flags=re.IGNORECASE)
    
    # 3. Handle negative contractions like "don't" -> "do n't"
    text = re.sub(r"(n't)\b", r" \1", text, flags=re.IGNORECASE)
    
    # 4. Clean up any accidental double spaces
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

def smart_detokenize(text):
    # 1. Stitch numbers back together (e.g., "3 . 14" -> "3.14")
    # This looks for a digit, followed by spaces, a period/comma, spaces, and another digit
    text = re.sub(r'(\d)\s+([.,])\s+(\d)', r'\1\2\3', text)
    
    # 2. Remove the space right before common punctuation
    text = re.sub(r'\s+([.,!?])', r'\1', text)
    
    # 3. Remove the space right before contractions
    text = re.sub(r"\s+('m|'s|'re|'ve|'ll|'d|n't)\b", r"\1", text, flags=re.IGNORECASE)
    
    return text

@app.post("/api/signin")
def signin(request: SignInRequest) -> dict:
  try:
    user = User(email=request.email)
    userDoc = db.collection('users').where('email', '==', user.email).get()
    if len(userDoc) == 0:
      return Response(status=status.HTTP_404_NOT_FOUND, data=None, message="You haven't signed up yet.").json()
    
    user_data = userDoc[0].to_dict()
    user.user_id = userDoc[0].id
    user.username = user_data['username']
    
    return Response(status=status.HTTP_200_OK, data=user.detail()).json()
  except:
    return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR, data=None, message="Failed").json()


@app.post("/api/signup")
def signup(request: SignUpRequest) -> dict:
  try:
    user = User(email=request.email, username=request.username)
    docs = db.collection('users').where('email', '==', user.email).get()
    if len(docs) > 0:
      return Response(status=status.HTTP_409_CONFLICT, data=None, message="Email already exists").json()

    user_doc = db.collection('users').document()
    user_doc.set({
      'email': user.email,
      'username': user.username
    })

    return Response(status=status.HTTP_201_CREATED, data={"user_id": user_doc.id}).json()
  except:
    return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR, data=None, message="Failed").json()


@app.post("/api/history")
def history(request: HistoryRequest) -> dict:
  try:
    if not request.user_id:
      return Response(status=status.HTTP_400_BAD_REQUEST, data=None, message="You have not yet signed in.").json()

    correctionDocs = db.collection('users').document(request.user_id).collection('corrections').order_by('created_at', direction=firestore.Query.DESCENDING).limit(20)
    corrections = []
    
    for subdoc in correctionDocs.stream():
      details = []
      current = subdoc.to_dict()
      current["correction_id"] = subdoc.id
      
      for subsubdoc in db.collection('users').document(request.user_id).collection('corrections').document(subdoc.id).collection('correction_details').stream():
        details.append(subsubdoc.to_dict())
      
      current['correction_details'] = details
      corrections.append(current)
    
    return Response(status=status.HTTP_200_OK, data=corrections).json()    
  except:
    return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR, data=None, message="Failed").json()


@app.post("/api/save")
def save_corrections(request: SaveRequest) -> dict:
  try:
    if not request.user_id:
      return Response(status=status.HTTP_400_BAD_REQUEST, data=None, message="You have not yet signed in.").json()

    user_doc = db.collection('users').document(request.user_id)
    saved_ids = []

    for correction_id in request.correction_id:
      correction_doc = user_doc.collection('corrections').document(correction_id)
      correction_doc.update({"is_saved": True})
      saved_ids.append(correction_id)

    return Response(status=status.HTTP_200_OK, data={"saved_ids": saved_ids}).json()
  except:
    return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR, data=None, message="Failed").json()


@app.post("/api/saved")
def saved_corrections(request: SavedRequest) -> dict:
  try:
    if not request.user_id:
      return Response(status=status.HTTP_400_BAD_REQUEST, data=None, message="You have not yet signed in.").json()

    correctionDocs = db.collection('users').document(request.user_id).collection('corrections').where('is_saved', '==', True)
    corrections = []

    for subdoc in correctionDocs.stream():
      details = []
      current = subdoc.to_dict()
      current["id"] = subdoc.id

      for subsubdoc in db.collection('users').document(request.user_id).collection('corrections').document(subdoc.id).collection('correction_details').stream():
        details.append(subsubdoc.to_dict())

      current['correction_details'] = details
      corrections.append(current)

    return Response(status=status.HTTP_200_OK, data=corrections).json()
  except:
    return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR, data=None, message="Failed").json()


@app.post("/api/correct")
async def gec(req: GECRequest) -> dict:
  try:
    # Tokenize the input sentences using the smart tokenizer
    tokenized_sentences = [smart_tokenize(sentence) for sentence in req.sentences]
    
    # Fetch the inference result from GECTOR Model
    async with httpx.AsyncClient(timeout=60) as client:
      res = await client.post(settings.GECTOR_URL, json={
        "sentences": tokenized_sentences,
        "iteration_count": req.iteration_count
      })
      res.raise_for_status()

    infer_res = res.json()
    print(infer_res)

    # Construct the response
    corrections = []
    # model = "gemini-3.1-pro-preview"
    model = "gemini-3.5-flash"
    client = genai.Client(
      api_key=settings.GEMINI_API_KEY,
    )
    generate_content_config = types.GenerateContentConfig(
      thinking_config=types.ThinkingConfig(
        thinking_level="LOW",
      ),
    )
    
    if(infer_res["ok"]):
      i = 0
      for orig, corr in zip(req.sentences, infer_res["predictions"]):
        i+=1
        corr["sentence"] = smart_detokenize(corr["sentence"])
        prompt = f"""
I have this sentence: "{orig}" and the corrected version: "{corr["sentence"]}".
The sentence is in {corr["voice_type"]} voice.
Please generate ONLY the JSON: {{"voice_conversion": "", "corrections": [{{"error_type": "Verb Tense", "explanation": "", "orig_start": 0, "orig_end": 0, "corr_start": 0, "corr_end": 0, "correction": ""}}]}}.
"explanation" is one short sentence explaining the error briefly.
"orig_start" is the starting word position in the original sentence being modified.
"orig_end" is the ending word position in the original sentence being modified.
"corr_start" is the starting word position in the corrected sentence that will modify the error words in the original sentence.
"corr_end" is the ending word position in the corrected sentence that will modify the error in the original sentence.
"error_type" depends on the type of error, it can be: Verb Tense, Subject–Verb Agreement, Article Usage, Preposition Error, Plurality/Countability, Word Order Error, or it could be another value but make sure it is no longer than 1 word.
"correction" is the word that replaces the position of characters that will be replaced by this, this attribute is purely to speed up the history reading process for future needs.
"voice_conversion" is when the result of the corrected sentence when it is converted into {'active' if corr["voice_type"] == 'passive' else 'passive'} voice, make sure the result is truly in {'active' if corr["voice_type"] == 'passive' else 'passive'} voice, and if it cannot be converted then just return '-'.
The attribute "corrections" must always be an array of object, whether there are more than one corrections or just one.
The whole JSON must not contain any wrappers, just pure JSON.
Please make sure to only see the original sentence and the corrected version, DO NOT try correct it on your own.
        """
        
        print(prompt)
# For example, if the original sentence is "I love eat chicken fry" and the corrected sentence is "I love to eat fried chicken.", then the JSON result should look like this: {{"voice_conversion": "Eating fried chicken is being loved by Me.", "corrections": [{{"error_type": "1-2 Words Error Type", "explanation": "One sentence explanation.", "orig_start": 1, "orig_end": 2, "corr_start": 1, "corr_end": 3, "correction": "to"}}, {{"error_type": "1-2 Words Error Type", "explanation": "One sentence explanation.", "orig_start": 3, "orig_end": 4, "corr_start": 4, "corr_end": 5, "correction": "fried chicken"}}, {{"error_type": "1-2 Words Error Type", "explanation": "One sentence explanation.", "orig_start": 5, "orig_end": 5, "corr_start": 6, "corr_end": 6, "correction": "."}}]}}.
        contents = [
          types.Content(
            role="user",
            parts=[
              types.Part.from_text(text=prompt),
            ],
          ),
        ]

        gemini_res = client.models.generate_content(
          model=model,
          contents=contents,
          config=generate_content_config,
        )
      
        response = json.loads(gemini_res.text)
        print(response)
      
        # Append the correction details to the response
        corrections.append({
          "correction_id": None,
          "orig_sentence": orig,
          "corr_sentence": corr['sentence'],
          "voice_type": corr['voice_type'],
          "voice_analysis": response["voice_conversion"],
          "correction_details": response["corrections"],
        })

    
    
    if req.user_id:
      user_doc = db.collection('users').document(req.user_id)
      for correction in corrections:
        correction_doc = user_doc.collection('corrections').document()
        correction_doc.set({
          "orig_sentence": correction["orig_sentence"],
          "corr_sentence": correction["corr_sentence"],
          "voice_type": correction["voice_type"],
          "voice_analysis": correction["voice_analysis"],
          "is_saved": False,
          "created_at": datetime.utcnow().isoformat() + "Z",
        })

        correction["correction_id"] = correction_doc.id

        for detail in correction["correction_details"]:
          detail_doc = correction_doc.collection('correction_details').document()
          detail_doc.set({
            "error_type": detail.get("error_type"),
            "explanation": detail.get("explanation"),
            "orig_start": detail.get("orig_start"),
            "orig_end": detail.get("orig_end"),
            "corr_start": detail.get("corr_start"),
            "corr_end": detail.get("corr_end"),
          })
    
    return Response(status=status.HTTP_200_OK, data=corrections, message="GEC inference successful").json()
  except Exception as e:
    print(e)
    return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR, data=None, message="failed").json()