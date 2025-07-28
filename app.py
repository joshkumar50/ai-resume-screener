# --- IMPORTS ---
import os
import fitz
import sqlite3
import datetime
import requests
from flask import Flask, request, jsonify, render_template, redirect, url_for

# --- INITIALIZE THE APP ---
app = Flask(__name__)

# --- GET THE API KEY and SETUP THE API ---
# Make sure you have set HUGGINGFACE_API_KEY in Render's Environment Variables
API_TOKEN = os.environ.get('HUGGINGFACE_API_KEY')
API_URL = "https://api-inference.huggingface.co/models/sentence-transformers/all-MiniLM-L6-v2"
headers = {"Authorization": f"Bearer {API_TOKEN}"}

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

# <<< --- FIX 1 (SOLVED): INITIALIZE THE DATABASE ON STARTUP --- >>>
# This ensures the tables exist when the app starts on the server.
init_db()
# <<< --- END OF FIX --- >>>


# --- HELPER FUNCTIONS ---
def extract_text_from_pdf(pdf_path):
    try:
        doc = fitz.open(pdf_path); text = "";
        for page in doc: text += page.get_text()
        return text
    except Exception as e: return f"Error reading PDF: {e}"

def calculate_similarity_via_api(resume_text, jd_text):
    if not API_TOKEN:
        print("ERROR: HUGGINGFACE_API_KEY environment variable not set.")
        return 0
        
    payload = {
        "inputs": {
            "source_sentence": jd_text,
            "sentences": [resume_text]
        }
    }
    response = requests.post(API_URL, headers=headers, json=payload)
    if response.status_code == 200:
        return response.json()[0]
    else:
        print(f"API Error: {response.status_code} - {response.text}")
        return 0

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

@app.route('/delete_candidate/<int:candidate_id>', methods=['POST'])
def delete_candidate(candidate_id):
    # <<< --- FIX 2 (SOLVED): ADDED conn.row_factory TO PREVENT TypeError --- >>>
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row  # This line ensures you can access columns by name
    cursor = conn.cursor()
    # <<< --- END OF FIX --- >>>

    cursor.execute('SELECT job_id FROM candidates WHERE id = ?', (candidate_id,)); candidate = cursor.fetchone()
    if candidate:
        cursor.execute('DELETE FROM candidates WHERE id = ?', (candidate_id,)); conn.commit()
        # This now works correctly because of the row_factory setting above
        job_id_to_redirect = candidate['job_id'] 
        conn.close()
        return redirect(url_for('view_rankings', job_id=job_id_to_redirect))
    
    conn.close()
    return redirect(url_for('index'))

@app.route('/match', methods=['POST'])
def match():
    resume_files = request.files.getlist('resume')
    jd_id = request.form.get('jd_id')

    if not resume_files or (len(resume_files) == 1 and resume_files[0].filename == ''):
        return jsonify({'error': 'No resume file uploaded'}), 400
    if not jd_id:
        return jsonify({'error': 'No job description selected'}), 400

    conn = sqlite3.connect('database.db'); conn.row_factory = sqlite3.Row; cursor = conn.cursor()
    cursor.execute('SELECT description FROM job_descriptions WHERE id = ?', (jd_id,))
    jd_row = cursor.fetchone()
    if not jd_row: conn.close(); return jsonify({'error': 'Job description not found'}), 404
    job_description = jd_row['description']
    
    processed_count = 0
    upload_folder = 'uploads' # Define upload folder
    if not os.path.exists(upload_folder): os.makedirs(upload_folder)

    for resume_file in resume_files:
        if resume_file.filename == '': continue
        
        resume_path = os.path.join(upload_folder, resume_file.filename); resume_file.save(resume_path)
        
        resume_text = extract_text_from_pdf(resume_path)
        if "Error" in resume_text: 
            os.remove(resume_path)
            continue

        similarity_score = calculate_similarity_via_api(resume_text, job_description)
        match_percentage = round(similarity_score * 100, 2)
        
        extracted_skills = "N/A"
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

# --- RUN THE APP FOR LOCAL TESTING ---
if __name__ == "__main__":
    app.run(debug=True, port=5001)