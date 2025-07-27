# --- IMPORTS ---
import os
import fitz
import sqlite3
import datetime
import spacy
from flask import Flask, request, jsonify, render_template, redirect, url_for
from sentence_transformers import SentenceTransformer, util

# --- INITIALIZE THE APP AND MODELS ---
app = Flask(__name__)
semantic_model = SentenceTransformer('all-MiniLM-L6-v2')
nlp = spacy.load("en_core_web_sm")

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_descriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL
        );
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            match_percentage REAL NOT NULL,
            skills TEXT,
            timestamp DATETIME NOT NULL,
            job_id INTEGER NOT NULL,
            FOREIGN KEY (job_id) REFERENCES job_descriptions (id)
        );
    ''')
    conn.commit()
    conn.close()

# --- HELPER FUNCTIONS ---
def extract_text_from_pdf(pdf_path):
    try:
        doc = fitz.open(pdf_path); text = ""; 
        for page in doc: text += page.get_text()
        return text
    except Exception as e: return f"Error reading PDF: {e}"

def calculate_similarity(resume_text, jd_text):
    embedding1 = semantic_model.encode(resume_text, convert_to_tensor=True)
    embedding2 = semantic_model.encode(jd_text, convert_to_tensor=True)
    similarity_score = util.cos_sim(embedding1, embedding2); return similarity_score.item()

def extract_skills(resume_text):
    SKILLS_LIST = [ 'python', 'java', 'c++', 'javascript', 'html', 'css', 'sql', 'git', 'react', 'vue', 'angular', 'aws', 'azure', 'docker', 'kubernetes', 'tensorflow', 'pytorch', 'scikit-learn', 'pandas', 'data analysis', 'project management', 'agile', 'scrum', 'team leadership' ]
    doc = nlp(resume_text.lower()); found_skills = set()
    for skill in SKILLS_LIST:
        if skill in doc.text: found_skills.add(skill.title())
    return ', '.join(found_skills)


# --- API ENDPOINTS ---
@app.route('/')
def index():
    conn = sqlite3.connect('database.db'); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
    cursor.execute('SELECT * FROM job_descriptions ORDER BY title')
    job_descriptions = cursor.fetchall(); conn.close()
    return render_template('index.html', job_descriptions=job_descriptions)

@app.route('/rankings/<int:job_id>')
def view_rankings(job_id):
    conn = sqlite3.connect('database.db'); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
    cursor.execute('SELECT * FROM job_descriptions WHERE id = ?', (job_id,)); job = cursor.fetchone()
    cursor.execute('SELECT * FROM candidates WHERE job_id = ? ORDER BY match_percentage DESC', (job_id,))
    candidates = cursor.fetchall(); conn.close()
    if not job: return "Job not found", 404
    return render_template('rankings.html', job=job, candidates=candidates)

@app.route('/add_jd', methods=['GET', 'POST'])
def add_jd():
    if request.method == 'POST':
        title = request.form['title']; description = request.form['description']
        conn = sqlite3.connect('database.db'); cursor = conn.cursor()
        cursor.execute('INSERT INTO job_descriptions (title, description) VALUES (?, ?)', (title, description))
        conn.commit(); conn.close()
        return redirect(url_for('index'))
    return render_template('add_jd.html')

@app.route('/delete_jd/<int:job_id>', methods=['POST'])
def delete_jd(job_id):
    conn = sqlite3.connect('database.db'); cursor = conn.cursor()
    cursor.execute('DELETE FROM candidates WHERE job_id = ?', (job_id,))
    cursor.execute('DELETE FROM job_descriptions WHERE id = ?', (job_id,))
    conn.commit(); conn.close()
    return redirect(url_for('index'))

# --- NEW FUNCTION TO DELETE A SINGLE CANDIDATE ---
@app.route('/delete_candidate/<int:candidate_id>', methods=['POST'])
def delete_candidate(candidate_id):
    """Deletes a single candidate screening from the database."""
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row # Important to fetch rows that we can access by column name
    cursor = conn.cursor()
    
    # First, get the job_id for the candidate we are about to delete.
    # We need this to know which ranking page to redirect back to.
    cursor.execute('SELECT job_id FROM candidates WHERE id = ?', (candidate_id,))
    candidate = cursor.fetchone()
    
    # Now, delete the candidate record itself
    cursor.execute('DELETE FROM candidates WHERE id = ?', (candidate_id,))
    
    conn.commit()
    conn.close()
    
    # If we successfully found the candidate's job_id, redirect back to that ranking page.
    if candidate:
        return redirect(url_for('view_rankings', job_id=candidate['job_id']))
    else:
        # As a fallback, if the candidate didn't exist, just go home.
        return redirect(url_for('index'))

# --- END OF NEW FUNCTION ---


@app.route('/match', methods=['POST'])
def match():
    resume_files = request.files.getlist('resume')
    jd_id = request.form.get('jd_id')

    if not resume_files or (len(resume_files) == 1 and resume_files[0].filename == ''):
        return jsonify({'error': 'No resume files uploaded'}), 400
    if not jd_id:
        return jsonify({'error': 'No job description selected'}), 400

    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT description FROM job_descriptions WHERE id = ?', (jd_id,))
    jd_row = cursor.fetchone()
    if not jd_row:
        conn.close()
        return jsonify({'error': 'Job description not found'}), 404
    job_description = jd_row['description']
    
    processed_count = 0

    for resume_file in resume_files:
        if resume_file.filename == '':
            continue
            
        upload_folder = 'uploads'
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)
        
        resume_path = os.path.join(upload_folder, resume_file.filename)
        resume_file.save(resume_path)
        
        resume_text = extract_text_from_pdf(resume_path)
        if "Error" in resume_text:
            os.remove(resume_path)
            continue

        similarity_score = calculate_similarity(resume_text, job_description)
        match_percentage = round(similarity_score * 100, 2)
        extracted_skills = extract_skills(resume_text)
        
        timestamp = datetime.datetime.now()
        cursor.execute(
            'INSERT INTO candidates (filename, match_percentage, skills, timestamp, job_id) VALUES (?, ?, ?, ?, ?)',
            (resume_file.filename, match_percentage, extracted_skills, timestamp, jd_id)
        )
        
        os.remove(resume_path)
        processed_count += 1
    
    conn.commit()
    conn.close()

    return jsonify({'message': f'Successfully processed {processed_count} of {len(resume_files)} resumes.'})


# --- RUN THE APP ---
if __name__ == "__main__":
    init_db()
    app.run(debug=True)