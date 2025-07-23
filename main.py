import re
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

# Initialize FastAPI app
app = FastAPI()

# --- Pydantic Models for API Input and Output ---

class Input(BaseModel):
    """Defines the structure for the API's request body."""
    text: str  # The full content of the isolation procedure document (extracted from PDF)
    query: Optional[str] = None  # The procedure code (e.g., MEXIP01) or an error description

class Output(BaseModel):
    """The output structure for the API's response."""
    procedure: Optional[str] = None  # The found procedure code
    steps: Optional[str] = None      # The raw text block of the procedure steps
    message: str                     # A descriptive message about the result

# --- Helper Function for Text Normalization ---

def _normalize_text_for_parsing(text: str) -> str:
    """
    Normalizes text to handle common PDF extraction issues while preserving line structure:
    - Replaces various unicode whitespace characters with standard space.
    - Replaces multiple spaces within a line with a single space.
    - Strips leading/trailing whitespace from each line.
    - Consolidates multiple blank lines into a single blank line.
    """
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        # Replace unicode spaces and multiple spaces with a single space within the line
        line = re.sub(r'[\u00A0\u200B\u2003\u2009\s]+', ' ', line).strip()
        if line: # Only add non-empty lines
            cleaned_lines.append(line)
    
    # Join lines and then consolidate multiple blank lines
    cleaned_text = '\n'.join(cleaned_lines)
    cleaned_text = re.sub(r'\n{2,}', '\n\n', cleaned_text) # Consolidate 2 or more newlines into two
    return cleaned_text.strip()

# --- Core Logic for Finding Procedure Steps ---

def find_procedure_steps(text: str, procedure_code: str) -> Optional[str]:
    """
    Finds the raw text block for a given procedure code.
    The regex captures everything from the procedure code up to the next
    procedure code or the end of the document.
    """
    procedure_code = procedure_code.strip().upper()
    escaped_code = re.escape(procedure_code)

    # More flexible pattern to capture the entire block for a procedure code
    # It looks for the procedure code, then non-greedily captures any characters (including newlines)
    # until it finds the start of another procedure code (e.g., MEXIP02, MEXIP03) or the end of the text.
    # The next code pattern is flexible: starts with 3+ uppercase letters, then 2+ digits, on a new line.
    pattern = re.compile(
        rf"({escaped_code}\s*.*?)(?=\n\s*[A-Z]{{3,}}[0-9]{{2,}}|\Z)",
        re.DOTALL
    )
    
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    return None

# --- FastAPI Endpoint ---

@app.post("/search-isolation-procedure", response_model=Output)
def search_isolation_procedure(payload: Input):
    """
    Searches for an isolation procedure by code or inferred error description
    and returns its raw text steps.
    """
    text = payload.text
    query = (payload.query or "").strip()

    # Normalize the input text before processing to handle PDF extraction quirks
    normalized_text = _normalize_text_for_parsing(text)

    if not query:
        return Output(
            procedure=None,
            steps=None,
            message="Please provide a procedure code or a description of the error to search."
        )

    # --- Basic Inference Logic (added functionality) ---
    # This maps common error descriptions to known procedure codes.
    error_to_procedure_map = {
        "invalid|not supported": "MEXIP01",
        "not detected|missing": "MEXIP02",
        "power problem|power issue": "MEXIP03",
        # Add more mappings as needed
    }
    
    inferred_procedure_code = None
    # First, try to infer the procedure code from the query's keywords
    for keywords, code in error_to_procedure_map.items():
        if re.search(keywords, query, re.IGNORECASE):
            inferred_procedure_code = code
            break
    
    # If no inference, check if the query itself is a procedure code
    if not inferred_procedure_code:
        if re.match(r"MEXIP\d{2}", query, re.IGNORECASE):
            inferred_procedure_code = query.upper()
    
    # --- Execute Search based on inferred or direct code ---
    if inferred_procedure_code:
        result_steps_text = find_procedure_steps(normalized_text, inferred_procedure_code)
        
        if result_steps_text:
            # Optionally, you could try to extract a brief description from the result_steps_text
            # For this simplified version, we'll just return the full block.
            return Output(
                procedure=inferred_procedure_code,
                steps=result_steps_text,
                message=f"Procedure '{inferred_procedure_code}' found. Full text of steps provided."
            )
        else:
            return Output(
                procedure=inferred_procedure_code,
                steps=None,
                message=f"Procedure '{inferred_procedure_code}' not found in the provided text."
            )
    else:
        # If no procedure code could be inferred or directly matched from the query
        return Output(
            procedure=None,
            steps=None,
            message=f"Could not find a relevant procedure for the query '{query}'. Please try a specific procedure code (e.g., MEXIP01) or a more detailed error description."
        )

