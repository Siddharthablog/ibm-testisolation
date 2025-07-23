import re
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional

# Initialize FastAPI app
app = FastAPI()

# --- Pydantic Models for API Input and Output ---

class Input(BaseModel):
    """Defines the structure for the API's request body."""
    text: str  # The full content of the isolation procedure document
    query: Optional[str] = None  # The procedure code (e.g., MEXIP01) or an error description

class StepDetail(BaseModel):
    """Represents a single, structured step within a procedure."""
    step_number: str
    instruction: str
    yes_action: Optional[str] = None
    no_action: Optional[str] = None
    continue_to_step: Optional[str] = None
    continue_to_procedure: Optional[str] = None
    ends_procedure: bool = False

class ProcedureDetail(BaseModel):
    """Represents a fully parsed isolation procedure."""
    code: str
    title: Optional[str] = None
    description: Optional[str] = None
    steps: List[StepDetail] = []

class Output(BaseModel):
    """The output structure for the API's response."""
    original_query: Optional[str] = None
    message: str
    found_procedure_code: Optional[str] = None
    procedure_details: Optional[ProcedureDetail] = None
    suggested_action: Optional[str] = None

# --- Helper Functions for Text Parsing ---

def find_procedure_block(text: str, procedure_code: str) -> Optional[str]:
    """
    Finds the raw text block for a given procedure code.
    The regex captures everything from the procedure code up to the next
    procedure code or the end of the document.
    """
    procedure_code = procedure_code.strip().upper()
    escaped_code = re.escape(procedure_code)

    # Updated pattern to be more flexible with whitespace around the code
    # and to capture until the next code or end of string.
    pattern = re.compile(
        rf"({escaped_code}\s*.*?)(?=\n\s*[A-Z0-9]{{6,}}(?:\s|$)|\Z)",
        re.DOTALL
    )
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    return None

def parse_steps_from_text(steps_text: str) -> List[StepDetail]:
    """
    Parses a raw text block containing steps into a list of StepDetail objects.
    Uses a single, robust regex to find each step.
    """
    parsed_steps: List[StepDetail] = []
    
    # Regex to find each step:
    # ^\s*(\d+)\.\s* : Matches step number (e.g., "1.") at the start of a line, captures number.
    # (.*?) : Non-greedy capture of the instruction text.
    # (?=\n\s*\d+\.|\Z) : Positive lookahead for the start of the next step or end of string.
    step_pattern = re.compile(
        r"^\s*(\d+)\.\s*(.*?)(?=\n\s*\d+\.|\Z)",
        re.DOTALL | re.MULTILINE
    )
    
    for match in step_pattern.finditer(steps_text):
        step_number = match.group(1).strip()
        step_content = match.group(2).strip()
        
        instruction = step_content
        yes_action, no_action, continue_to_step, continue_to_procedure, ends_procedure = None, None, None, None, False
        
        # Regex to find Yes/No blocks within the step content
        # This now looks for "Yes:" or "No:" at the beginning of a line within the step content
        yes_no_split = re.split(r"^(Yes:|No:)", step_content, flags=re.MULTILINE | re.IGNORECASE)
        
        if len(yes_no_split) > 1: # If Yes/No split found
            instruction = yes_no_split[0].strip() # Instruction is before the first Yes/No
            
            # Iterate through the split parts to find Yes/No actions
            for i in range(1, len(yes_no_split), 2):
                action_type = yes_no_split[i].strip().lower()
                action_content = yes_no_split[i+1].strip()
                
                if action_type == "yes:":
                    yes_action = action_content
                elif action_type == "no:":
                    no_action = action_content
        
        # Find explicit flow control instructions
        continue_step_match = re.search(r"continue with step “?(\d+)”?", step_content, re.IGNORECASE)
        if continue_step_match:
            continue_to_step = continue_step_match.group(1)

        continue_proc_match = re.search(r"Use procedure “?([A-Z0-9]+)”?", step_content)
        if continue_proc_match:
            continue_to_procedure = continue_proc_match.group(1)
            
        if re.search(r"This ends the procedure\.", step_content, re.IGNORECASE):
            ends_procedure = True
            
        parsed_steps.append(StepDetail(
            step_number=step_number,
            instruction=instruction,
            yes_action=yes_action,
            no_action=no_action,
            continue_to_step=continue_to_step,
            continue_to_procedure=continue_to_procedure,
            ends_procedure=ends_procedure
        ))
        
    return parsed_steps

def parse_procedure_block(procedure_text: str, procedure_code: str) -> ProcedureDetail:
    """Parses a raw procedure text block into a structured ProcedureDetail."""
    description = ""
    steps_raw_text = procedure_text

    # Try to find the description (text between procedure code and "Procedure" heading)
    # More flexible regex for "Procedure" heading
    description_match = re.search(
        rf"^{re.escape(procedure_code)}\s*\n(.*?)(?=\n\s*Procedure\s*\n)",
        procedure_text, re.DOTALL | re.MULTILINE
    )
    if description_match:
        description = description_match.group(1).strip()
        steps_raw_text = procedure_text[description_match.end():]
        # Remove the "Procedure" heading and any text before the first step
        steps_raw_text = re.sub(r"^\s*Procedure\s*\n", "", steps_raw_text, 1, re.MULTILINE)
        # Further clean up any non-step text at the beginning of steps_raw_text
        # This ensures parse_steps_from_text starts directly with steps
        match_first_step = re.search(r"^\s*\d+\.", steps_raw_text, re.MULTILINE)
        if match_first_step:
            steps_raw_text = steps_raw_text[match_first_step.start():]
        else:
            steps_raw_text = "" # No steps found after description

    # Use the new, robust step parsing function
    parsed_steps = parse_steps_from_text(steps_raw_text)

    # Extract a title from the first line of the description, or use a default
    title = description.split('\n')[0].strip() if description else f"Isolation Procedure {procedure_code}"

    return ProcedureDetail(
        code=procedure_code,
        title=title,
        description=description,
        steps=parsed_steps
    )

def get_suggested_action(query: str, procedure_details: ProcedureDetail) -> Optional[str]:
    """
    Simulates NLP by suggesting a relevant action based on keywords in the query.
    This is a rule-based heuristic.
    """
    lower_query = query.lower()
    
    # Try to find the most relevant step based on the query keywords
    for step in procedure_details.steps:
        lower_instruction = step.instruction.lower()
        
        # Rule 1: Match for problems with I/O module validity (MEXIP01)
        if procedure_details.code == "MEXIP01" and ("invalid" in lower_query or "not supported" in lower_query):
            if "location code" in lower_instruction and step.step_number == "1":
                if step.no_action:
                    return f"Based on the issue, start with step {step.step_number}: '{step.instruction}'. If a location code is not available, the recommended action is: '{step.no_action}'."
                else:
                    return f"Based on the issue, start with step {step.step_number}: '{step.instruction}'."
        
        # Rule 2: Match for problems with missing or undetected I/O modules (MEXIP02)
        if procedure_details.code == "MEXIP02" and ("not detected" in lower_query or "missing" in lower_query):
            if "required i/o module or enclosure services manager" in lower_instruction and step.step_number == "1":
                 if step.no_action:
                     return f"Based on the issue, start with step {step.step_number}: '{step.instruction}'. If the module is not detected, follow the instructions for the 'No' case: '{step.no_action}'."
                 else:
                     return f"Based on the issue, start with step {step.step_number}: '{step.instruction}'."
            if "present and properly seated" in lower_instruction:
                 if step.no_action:
                     return f"Based on the issue, check step {step.step_number}: '{step.instruction}'. If the module is not present or properly seated, the recommended action is: '{step.no_action}'."
                 else:
                     return f"Based on the issue, check step {step.step_number}: '{step.instruction}'."

        # Rule 3: Match for power-related issues (MEXIP03)
        if procedure_details.code == "MEXIP03" and "power problem" in lower_query:
            if "verify the following led states" in lower_instruction and step.step_number == "4":
                return f"Based on the issue, check step {step.step_number}: '{step.instruction}'. You need to verify the LED states on the power supplies."

    # If no specific rule matches, provide a generic suggestion.
    if procedure_details.steps:
        return f"Please review the full procedure starting with step 1: '{procedure_details.steps[0].instruction}'."
    
    return "No specific action could be suggested from the query."

# --- FastAPI Endpoint ---

@app.post("/search-isolation-procedure", response_model=Output)
def search_isolation_procedure(payload: Input):
    """
    Searches for an isolation procedure and provides its parsed steps,
    along with a basic NLP-driven action suggestion for error fixing.
    """
    text = payload.text
    query = (payload.query or "").strip()
    
    if not query:
        return Output(
            original_query=query,
            message="Please provide a procedure code or a description of the error to search.",
            suggested_action="No query provided."
        )

    # Dictionary for mapping error keywords to a procedure code
    error_to_procedure_map = {
        "invalid|not supported": "MEXIP01",
        "not detected|missing": "MEXIP02",
        "power problem|power issue": "MEXIP03",
    }
    
    # Try to infer a procedure code from the query first
    procedure_code_from_query = None
    for keywords, code in error_to_procedure_map.items():
        if re.search(keywords, query, re.IGNORECASE):
            procedure_code_from_query = code
            break
    
    # If no inference, check if the query is a procedure code itself
    if not procedure_code_from_query:
        if re.match(r"MEXIP\d{2}", query, re.IGNORECASE):
            procedure_code_from_query = query.upper()
    
    # If a code was found (either by inference or direct query)
    if procedure_code_from_query:
        procedure_block_text = find_procedure_block(text, procedure_code_from_query)

        if procedure_block_text:
            procedure_details = parse_procedure_block(procedure_block_text, procedure_code_from_query)
            suggested_action = get_suggested_action(query, procedure_details)

            return Output(
                original_query=query,
                message=f"Procedure '{procedure_code_from_query}' found and parsed.",
                found_procedure_code=procedure_code_from_query,
                procedure_details=procedure_details,
                suggested_action=suggested_action
            )

    # If no procedure block was found after all attempts
    return Output(
        original_query=query,
        message=f"Procedure for query '{query}' not found or could not be inferred. Please try a specific procedure code (e.g., MEXIP01) or a more detailed error description.",
        suggested_action="Could not find a relevant procedure for the given query."
    )

