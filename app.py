# Imports required for the application
from docx import Document # For working with .docx files
import re # For regular expressions (text search and manipulation)
import json # For working with JSON data (saving triplets)
from collections import defaultdict # For counters and storing duplicates
import os # For working with file paths and creating directories
import streamlit as st # The main library for the web application
from io import BytesIO # For handling in-memory files uploaded by Streamlit

# --- Core Logic Functions ---
# These functions contain the logic for parsing the document,
# extracting data, and generating output files.
# They are defined outside the `if __name__ == "__main__":` block,
# so they can be called cleanly by the Streamlit UI.

def extract_articles_from_docx(doc_path):
    """
    Extracts article blocks from a DOCX document based on a specific marker.
    Each block is interpreted to contain a narrative paragraph followed by
    transition phrases.

    The function assumes that each article block starts with the line
    "À savoir également dans votre département" (or rather, this line marks
    the end of the metadata part of the previous article and the beginning of the content
    of a new article). The first text paragraph after this marker is considered the
    'narrative_paragraph', and the subsequent paragraphs are assumed to be the
    'transitions_list' for that narrative paragraph.

    Args:
        doc_path (str): The path to the DOCX file.

    Returns:
        list: A list of dictionaries, where each dictionary contains the 'narrative_paragraph'
              and the 'transitions_list' of an article.
              Example: [{'narrative_paragraph': '...', 'transitions_list': ['...', '...']}]
    """
    try:
        doc = Document(doc_path)
    except Exception as e:
        st.error(f"Error loading the DOCX document: {e}")
        return []

    all_raw_blocks = [] # This will collect ALL blocks separated by the marker
    current_block_content = []
    
    for para in doc.paragraphs:
        text = para.text.strip()
        
        if "À savoir également dans votre département" in text:
            if current_block_content: # If there's content before this marker, add it as a block
                all_raw_blocks.append(current_block_content)
            current_block_content = [] # Reset for the next block (which starts after this marker)
            continue # Skip the marker line itself
        elif text: # Add non-empty lines to the current block
            current_block_content.append(text)
            
    if current_block_content: # Add the very last block after the loop
        all_raw_blocks.append(current_block_content)

    parsed_articles = []
    # The first block in `all_raw_blocks` will contain the header/title/blurb
    # before the *first* "À savoir..." marker. This block should be ignored.
    # We iterate from the second block onwards.
    
    # If all_raw_blocks is empty or only contains the initial ignored block, loop won't run.
    # We need at least 2 blocks for a valid article (1 for header, 1 for actual content).
    if len(all_raw_blocks) < 2:
        return [] # No valid articles found

    for block in all_raw_blocks[1:]: # Start from the second block (index 1)
        # A valid article block must have at least a narrative paragraph and one transition line
        # (as per manual analysis, some articles only have 1 transition listed).
        if len(block) < 2: 
            continue

        narrative_paragraph = block[0]
        # All subsequent lines in the block are considered potential transitions.
        transitions_list_candidates = block[1:] 

        parsed_articles.append({
            'narrative_paragraph': narrative_paragraph,
            'transitions_list': [t.strip() for t in transitions_list_candidates if t.strip()] # `.strip()` for cleanliness
        })
    return parsed_articles


def extract_all_raw_triplets(narrative_paragraph, transitions_list):
    """
    Extracts all possible (paragraph_a, transition, paragraph_b) triplets
    for a given narrative paragraph and its associated list of transitions.
    The function is robust against certain variations in transitions (e.g., [XXX], qu').

    Args:
        narrative_paragraph (str): The main paragraph where transitions are searched.
        transitions_list (list): A list of transition phrases to be found in the paragraph.

    Returns:
        list: A list of dictionaries, where each dictionary represents a triplet.
              Example: [{'paragraph_a': '...', 'transition': '...', 'paragraph_b': '...'}]
    """
    raw_triplets = []
    
    for transition_from_list in transitions_list:
        if not transition_from_list: # Skip empty transitions
            continue
        
        # Step 1: Create a robust regex pattern from the transition in the list
        # Starting with the exact, escaped string of the transition from the list.
        pattern_str = re.escape(transition_from_list)
        
        # Specific adjustments for known variations:
        
        # 1. Placeholder `[XXX]`: Replace `\[XXX\]` (escaped) by `[^,]*`
        #    `[^,]*` means: zero or more characters that are NOT a comma.
        #    This catches variable place names or similar (e.g., "[XXX]" -> "Lizy-sur-Ourcq").
        pattern_str = pattern_str.replace(re.escape("[XXX]"), r"[^,]*")
        
        # 2. French contractions at the end: ` que` vs ` qu'` + word
        #    If the transition ends with " que", adjust the pattern to allow both.
        #    `(?:\sque|\squ\'\w*)` matches:
        #      - `\sque` (whitespace followed by "que")
        #      - OR
        #      - `\squ\'\w*` (whitespace followed by "qu'", followed by zero or more word characters)
        #    `\w*` allows us to match "qu'un", "qu'une", "qu'il", etc.
        if pattern_str.endswith(re.escape(" que")):
            pattern_str = pattern_str[:-len(re.escape(" que"))] + r"(?:\sque|\squ\'\w*)"
        
        # Compile the regex pattern.
        # `re.IGNORECASE` makes the search case-insensitive.
        compiled_pattern = re.compile(pattern_str, re.IGNORECASE)
        
        # Find all non-overlapping occurrences of the transition in the narrative paragraph
        # `match.span()` returns the start and end indices of the found match.
        for match in compiled_pattern.finditer(narrative_paragraph):
            start, end = match.span()
            
            # Divide the narrative paragraph into three parts:
            # - Text before the transition
            # - The transition itself (as found IN THE TEXT, to show the exact match)
            # - Text after the transition
            
            paragraph_a = narrative_paragraph[:start].strip() # Text before the transition
            # The actually found transition in the text
            matched_transition_in_text = narrative_paragraph[start:end] 
            paragraph_b = narrative_paragraph[end:].strip() # Text after the transition
            
            raw_triplets.append({
                "paragraph_a": paragraph_a,
                # Important: The requirement was to list the transition as it appears in the initial list.
                # Therefore, we use `transition_from_list` here, not `matched_transition_in_text`.
                "transition": transition_from_list, 
                "paragraph_b": paragraph_b
            })
    return raw_triplets


def generate_output_files(all_raw_triplets, selected_outputs, output_dir=".", max_uses_per_transition=3):
    """
    Generates the various output files based on the extracted triplets
    and applies the maximum usage limit per transition.
    It also creates additional text files for transitions that exceed
    the usage limit.

    Args:
        all_raw_triplets (list): A list of all found triplets (before capping).
        selected_outputs (list): A list of the names of the output files to generate.
                                 Possible values: "fewshot_examples.json", "transitions_only.txt",
                                 "fewshot_examples.jsonl".
        output_dir (str): The directory where the output files should be saved.
                          Default is the current directory.
        max_uses_per_transition (int): The maximum number of uses per transition
                                       to be included in the main output files.

    Returns:
        tuple: A tuple containing a dictionary with information about the generated files
               and the number of entries in each file, as well as a list of the
               final valid triplets (after capping).
    """
    # Ensure variables are initialized
    generated_files_info = {}
    final_triplets = [] 

    try:
        os.makedirs(output_dir, exist_ok=True)

        transition_usage_tracker = defaultdict(int)
        duplicates_tracker = defaultdict(list)
        
        for triplet in all_raw_triplets:
            transition = triplet["transition"] 
            if transition_usage_tracker[transition] < max_uses_per_transition:
                final_triplets.append(triplet)
                transition_usage_tracker[transition] += 1
            else:
                duplicates_tracker[transition].append(triplet)
                
        # Generate fewshot_examples.json
        if "fewshot_examples.json" in selected_outputs:
            file_path = os.path.join(output_dir, "fewshot_examples.json")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(final_triplets, f, ensure_ascii=False, indent=4) 
            generated_files_info["fewshot_examples.json"] = len(final_triplets)

        # Generate fewshots rejected.txt
        rejected_fewshots_content = []
        for transition, duplicates in duplicates_tracker.items():
            if duplicates:
                actual_total_count = transition_usage_tracker[transition] + len(duplicates)
                rejected_fewshots_content.append(f"Transition: '{transition}' used {actual_total_count} times (exceeded by {len(duplicates)})")
        if "fewshots_rejected.txt" in selected_outputs and rejected_fewshots_content:
            file_path = os.path.join(output_dir, "fewshots_rejected.txt")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(rejected_fewshots_content))
            generated_files_info["fewshots_rejected.txt"] = len(rejected_fewshots_content)


        # Generate transitions_only.txt
        if "transitions_only.txt" in selected_outputs:
            unique_transitions = sorted(list(set(t["transition"] for t in final_triplets)))
            file_path = os.path.join(output_dir, "transitions_only.txt")
            with open(file_path, 'w', encoding='utf-8') as f:
                for transition in unique_transitions:
                    f.write(f"{transition}\n") 
            generated_files_info["transitions_only.txt"] = len(unique_transitions)

        # Generate transitions_only_rejected.txt
        rejected_transitions_only_content = []
        all_found_transitions_counts = defaultdict(int)
        for triplet in all_raw_triplets: 
            all_found_transitions_counts[triplet["transition"]] += 1

        for transition, count in all_found_transitions_counts.items():
            if count > 1: 
                rejected_transitions_only_content.append(f"Transition: '{transition}' used {count} times")
        
        if "transitions_only_rejected.txt" in selected_outputs and rejected_transitions_only_content:
            file_path = os.path.join(output_dir, "transitions_only_rejected.txt")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(rejected_transitions_only_content))
            generated_files_info["transitions_only_rejected.txt"] = len(rejected_transitions_only_content)


        # Generate fewshot_examples.jsonl (for AI fine-tuning)
        if "fewshot_examples.jsonl" in selected_outputs:
            file_path = os.path.join(output_dir, "fewshot_examples.jsonl")
            with open(file_path, 'w', encoding='utf-8') as f:
                for triplet in final_triplets:
                    jsonl_entry = {
                        "messages": [
                            {"role": "user", "content": f"{triplet['paragraph_a']} ... {triplet['paragraph_b']}"},
                            {"role": "assistant", "content": triplet['transition']}
                        ]
                    }
                    f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n") 
            generated_files_info["fewshot_examples.jsonl"] = len(final_triplets)
        
        # Generate fewshots-fineTuning_rejected.txt
        rejected_finetuning_content = []
        for transition, duplicates in duplicates_tracker.items():
            if duplicates:
                for dup_triplet in duplicates:
                    jsonl_entry = {
                        "messages": [
                            {"role": "user", "content": f"{dup_triplet['paragraph_a']} ... {dup_triplet['paragraph_b']}"},
                            {"role": "assistant", "content": dup_triplet['transition']}
                        ]
                    }
                    rejected_finetuning_content.append(json.dumps(jsonl_entry, ensure_ascii=False))
        
        if "fewshots-fineTuning_rejected.txt" in selected_outputs and rejected_finetuning_content:
            file_path = os.path.join(output_dir, "fewshots-fineTuning_rejected.txt")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(rejected_finetuning_content))
            generated_files_info["fewshots-fineTuning_rejected.txt"] = len(rejected_finetuning_content)

        return generated_files_info, final_triplets

    except Exception as e:
        # Catch internal errors and return a clearer error message
        raise RuntimeError(f"Error generating output files: {e}")


def simulate_streamlit_app_logic(doc_file_path, selected_outputs, output_dir="."):
    """
    Simulates the backend logic of a Streamlit application for extracting
    transition triplets. This function would be called by the Streamlit UI.

    Args:
        doc_file_path (str): The path to the uploaded DOCX file.
        selected_outputs (list): A list of output types selected by the user.
        output_dir (str): The directory where the output files should be saved.

    Returns:
        dict: A dictionary with information about the generated files and
              the number of valid extracted examples, or an error dictionary.
    """
    try:
        articles = extract_articles_from_docx(doc_file_path)
        
        all_raw_triplets = []
        for article in articles:
            narrative_p = article['narrative_paragraph']
            transitions_l = article['transitions_list']
            
            raw_triplets_for_article = extract_all_raw_triplets(narrative_p, transitions_l)
            all_raw_triplets.extend(raw_triplets_for_article)
            
        generated_info, final_valid_triplets = generate_output_files(all_raw_triplets, selected_outputs, output_dir)
        
        results_summary = {
            "generated_files": generated_info,
            "total_valid_examples": len(final_valid_triplets)
        }
        
        return results_summary

    except Exception as e:
        # This catches any error in the higher-level functions and returns it as
        # an error dictionary that the Streamlit UI can display.
        return {"error": f"An unexpected error occurred: {e}"}


# --- Streamlit UI Code ---
# This block is executed when you run `streamlit run app.py`.
# It contains all the interactive elements of the web application.

if __name__ == "__main__":
    st.set_page_config(layout="centered", page_title="Transition Triplet Extractor")
    st.title("📄 DOCX Transition Triplet Extractor")
    st.markdown("Upload a DOCX file containing regional French news articles to extract structured transition triplets.")

    uploaded_file = st.file_uploader("Upload your .docx file", type=["docx"])

    if uploaded_file is not None:
        st.success("File successfully uploaded!")
        
        # Create a temporary directory for output files if it doesn't exist
        output_dir = "extracted_output"
        os.makedirs(output_dir, exist_ok=True)

        # Save the uploaded file temporarily to disk.
        # Streamlit returns a BytesIO object, but python-docx needs a file path.
        temp_file_path = os.path.join(output_dir, uploaded_file.name)
        with open(temp_file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        
        st.subheader("Select outputs to generate:")
        col1, col2, col3, col4, col5, col6 = st.columns(6) 
        with col1:
            gen_json = st.checkbox("fewshot_examples.json", value=True)
        with col2:
            gen_rejected_txt = st.checkbox("fewshots_rejected.txt", value=True)
        with col3:
            gen_txt = st.checkbox("transitions_only.txt", value=True)
        with col4:
            gen_transitions_rejected_txt = st.checkbox("transitions_only_rejected.txt", value=True)
        with col5:
            gen_jsonl = st.checkbox("fewshot_examples.jsonl", value=True)
        with col6:
            gen_finetuning_rejected_txt = st.checkbox("fewshots-fineTuning_rejected.txt", value=True)


        selected_outputs = []
        if gen_json:
            selected_outputs.append("fewshot_examples.json")
        if gen_rejected_txt: 
            selected_outputs.append("fewshots_rejected.txt")
        if gen_txt:
            selected_outputs.append("transitions_only.txt")
        if gen_transitions_rejected_txt: 
            selected_outputs.append("transitions_only_rejected.txt")
        if gen_jsonl:
            selected_outputs.append("fewshot_examples.jsonl")
        if gen_finetuning_rejected_txt: 
            selected_outputs.append("fewshots-fineTuning_rejected.txt")


        if st.button("Extract Triplets and Generate Files"):
            if not selected_outputs:
                st.warning("Please select at least one output type.")
            else:
                with st.spinner("Extracting and generating files... Please wait a moment."):
                    results = simulate_streamlit_app_logic(temp_file_path, selected_outputs, output_dir)

                if "error" in results:
                    st.error(f"An error occurred: {results['error']}")
                else:
                    st.success("Extraction and generation complete!")
                    st.subheader("Results:")
                    st.info(f"**{results['total_valid_examples']}** valid examples extracted.")
                    
                    st.markdown("---")
                    st.subheader("Generated Files (saved locally in the `extracted_output` folder):")
                    st.write("You can download the files directly here or find them in the `extracted_output` subfolder in your project directory.")
                    
                    # Loop through the generated files and offer them for download
                    for file_name, count in results['generated_files'].items():
                        st.write(f"- `{file_name}`: {count} entries/duplicates")
                        file_path = os.path.join(output_dir, file_name)
                        try:
                            # Open the file in binary read mode for download
                            with open(file_path, "rb") as file_to_download:
                                st.download_button(
                                    label=f"Download {file_name}",
                                    data=file_to_download,
                                    file_name=file_name,
                                    mime="application/octet-stream", 
                                    key=f"download_{file_name}" 
                                )
                        except FileNotFoundError:
                            st.warning(f"File {file_name} could not be found. It might not have been generated.")

                    # Clean up the temporary .docx file after processing
                    try:
                        os.remove(temp_file_path)
                        st.info(f"Temporary uploaded file '{uploaded_file.name}' removed.")
                    except OSError as e:
                        st.warning(f"Could not remove temporary file '{uploaded_file.name}': {e}")

    else:
        st.info("Please upload a .docx file to get started.")
