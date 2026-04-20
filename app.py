from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Budget, Meeting, Task, MeetingAttendance, BudgetAttachment, BudgetHistory, GoogleCalendarToken
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import json
import os
import uuid
from werkzeug.utils import secure_filename
import csv

import os

# Add this line right after creating the app
app = Flask(__name__)

# Update database configuration for production
if os.environ.get('DATABASE_URL'):
    # For PostgreSQL on Render
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL').replace('postgres://', 'postgresql://')
else:
    # For local development
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'

from io import BytesIO, StringIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from openpyxl import Workbook



app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-this-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# File upload configuration
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'jpg', 'png', 'txt'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Create default admin user
def create_default_admin():
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            email='admin@business.com',
            password=generate_password_hash('admin123'),
            role='admin'
        )
        db.session.add(admin)
        
        # Create a sample staff user
        staff = User(
            username='staff',
            email='staff@business.com',
            password=generate_password_hash('staff123'),
            role='staff'
        )
        db.session.add(staff)
        db.session.commit()
        print("Default users created: admin/admin123, staff/staff123")

# Routes
@app.route('/')
@login_required
def dashboard():
    # Get statistics for dashboard
    pending_budgets = Budget.query.filter_by(status='pending').count()
    approved_budgets = Budget.query.filter_by(status='approved').count()
    upcoming_meetings = Meeting.query.filter(Meeting.date_time > datetime.utcnow()).order_by(Meeting.date_time).limit(5).all()
    my_tasks = Task.query.filter_by(assigned_to=current_user.id, status='pending').order_by(Task.due_date).limit(10).all()
    recent_budgets = Budget.query.order_by(Budget.created_at.desc()).limit(5).all()
    
    return render_template('dashboard.html',
                         pending_budgets=pending_budgets,
                         approved_budgets=approved_budgets,
                         upcoming_meetings=upcoming_meetings,
                         my_tasks=my_tasks,
                         recent_budgets=recent_budgets)

# Budget routes
@app.route('/budgets')
@login_required
def view_budgets():
    if current_user.role == 'admin':
        budgets = Budget.query.order_by(Budget.created_at.desc()).all()
    else:
        budgets = Budget.query.filter_by(submitted_by=current_user.id).order_by(Budget.created_at.desc()).all()
    return render_template('budgets.html', budgets=budgets)

@app.route('/budget/create', methods=['GET', 'POST'])
@login_required
def create_budget():
    if request.method == 'POST':
        budget = Budget(
            title=request.form['title'],
            description=request.form.get('description', ''),
            amount=float(request.form['amount']),
            department=request.form.get('department', ''),
            submitted_by=current_user.id,
            status='pending'
        )
        db.session.add(budget)
        db.session.commit()
        
        # Add to history
        history = BudgetHistory(
            budget_id=budget.id,
            action='created',
            comment='Budget created',
            user_id=current_user.id
        )
        db.session.add(history)
        db.session.commit()
        
        flash('Budget submitted successfully!', 'success')
        return redirect(url_for('view_budgets'))
    return render_template('create_budget.html')

@app.route('/budget/<int:id>')
@login_required
def view_budget(id):
    budget = Budget.query.get_or_404(id)
    if current_user.role != 'admin' and budget.submitted_by != current_user.id:
        flash('You do not have permission to view this budget.', 'danger')
        return redirect(url_for('view_budgets'))
    
    attachments = BudgetAttachment.query.filter_by(budget_id=id).all()
    history = BudgetHistory.query.filter_by(budget_id=id).order_by(BudgetHistory.timestamp.desc()).all()
    
    return render_template('view_budget.html', budget=budget, attachments=attachments, history=history)

@app.route('/budget/<int:id>/review', methods=['POST'])
@login_required
def review_budget(id):
    if current_user.role != 'admin':
        flash('Only admins can review budgets.', 'danger')
        return redirect(url_for('view_budgets'))
    
    budget = Budget.query.get_or_404(id)
    action = request.form.get('action')
    budget.review_comments = request.form.get('comments', '')
    budget.reviewed_by = current_user.id
    
    if action == 'approve':
        budget.status = 'approved'
        flash(f'Budget "{budget.title}" approved!', 'success')
        history_action = 'approved'
    elif action == 'reject':
        budget.status = 'rejected'
        flash(f'Budget "{budget.title}" rejected.', 'warning')
        history_action = 'rejected'
    else:
        history_action = 'reviewed'
    
    # Add to history
    history = BudgetHistory(
        budget_id=budget.id,
        action=history_action,
        comment=request.form.get('comments', ''),
        user_id=current_user.id
    )
    db.session.add(history)
    db.session.commit()
    
    return redirect(url_for('view_budgets'))

# Budget attachment routes
@app.route('/budget/<int:id>/upload', methods=['POST'])
@login_required
def upload_attachment(id):
    budget = Budget.query.get_or_404(id)
    
    if 'file' not in request.files:
        flash('No file selected', 'danger')
        return redirect(url_for('view_budget', id=id))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'danger')
        return redirect(url_for('view_budget', id=id))
    
    if file and allowed_file(file.filename):
        # Save file with unique name
        original_filename = secure_filename(file.filename)
        file_ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
        unique_filename = f"{uuid.uuid4().hex}.{file_ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(filepath)
        
        # Save to database
        attachment = BudgetAttachment(
            budget_id=budget.id,
            filename=unique_filename,
            original_filename=original_filename,
            file_size=os.path.getsize(filepath),
            file_type=file_ext,
            uploaded_by=current_user.id
        )
        db.session.add(attachment)
        
        # Add to history
        history = BudgetHistory(
            budget_id=budget.id,
            action='attachment_uploaded',
            comment=f'Uploaded: {original_filename}',
            user_id=current_user.id
        )
        db.session.add(history)
        db.session.commit()
        
        flash(f'File {original_filename} uploaded successfully!', 'success')
    else:
        flash('File type not allowed. Allowed: PDF, DOC, DOCX, XLS, XLSX, JPG, PNG, TXT', 'danger')
    
    return redirect(url_for('view_budget', id=id))

@app.route('/budget/attachment/<int:id>/download')
@login_required
def download_attachment(id):
    attachment = BudgetAttachment.query.get_or_404(id)
    budget = Budget.query.get(attachment.budget_id)
    
    # Check permissions
    if current_user.role != 'admin' and budget.submitted_by != current_user.id:
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], attachment.filename)
    if not os.path.exists(filepath):
        flash('File not found', 'danger')
        return redirect(url_for('view_budget', id=budget.id))
    
    return send_file(filepath, as_attachment=True, download_name=attachment.original_filename)

@app.route('/budget/attachment/<int:id>/delete')
@login_required
def delete_attachment(id):
    attachment = BudgetAttachment.query.get_or_404(id)
    budget = Budget.query.get(attachment.budget_id)
    
    # Check permissions
    if current_user.role != 'admin' and budget.submitted_by != current_user.id:
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    
    # Delete file
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], attachment.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    
    db.session.delete(attachment)
    db.session.commit()
    
    flash('Attachment deleted', 'success')
    return redirect(url_for('view_budget', id=budget.id))

# Meeting routes
@app.route('/meetings')
@login_required
def view_meetings():
    meetings = Meeting.query.order_by(Meeting.date_time.desc()).all()
    return render_template('meetings.html', meetings=meetings)

@app.route('/meeting/create', methods=['GET', 'POST'])
@login_required
def create_meeting():
    if request.method == 'POST':
        meeting = Meeting(
            title=request.form['title'],
            description=request.form.get('description', ''),
            date_time=datetime.strptime(request.form['date_time'], '%Y-%m-%dT%H:%M'),
            duration=int(request.form.get('duration', 60)),
            meeting_link=request.form.get('meeting_link', ''),
            location=request.form.get('location', ''),
            created_by=current_user.id
        )
        db.session.add(meeting)
        db.session.commit()
        
        # Add creator as confirmed attendee
        attendance = MeetingAttendance(
            meeting_id=meeting.id,
            user_id=current_user.id,
            status='confirmed'
        )
        db.session.add(attendance)
        db.session.commit()
        
        flash('Meeting scheduled successfully!', 'success')
        return redirect(url_for('view_meetings'))
    
    users = User.query.all()
    return render_template('create_meeting.html', users=users)

@app.route('/meeting/<int:id>')
@login_required
def view_meeting(id):
    meeting = Meeting.query.get_or_404(id)
    attendees = MeetingAttendance.query.filter_by(meeting_id=id).all()
    tasks = Task.query.filter_by(meeting_id=id).all()
    users = User.query.all()
    return render_template('view_meeting.html', meeting=meeting, attendees=attendees, tasks=tasks, users=users)

@app.route('/meeting/<int:id>/invite', methods=['POST'])
@login_required
def invite_to_meeting(id):
    meeting = Meeting.query.get_or_404(id)
    user_ids = request.form.getlist('user_ids')
    
    for user_id in user_ids:
        existing = MeetingAttendance.query.filter_by(meeting_id=id, user_id=int(user_id)).first()
        if not existing:
            attendance = MeetingAttendance(
                meeting_id=id,
                user_id=int(user_id),
                status='invited'
            )
            db.session.add(attendance)
    
    db.session.commit()
    flash('Invitations sent!', 'success')
    return redirect(url_for('view_meeting', id=id))

# Task routes
@app.route('/tasks')
@login_required
def view_tasks():
    if current_user.role == 'admin':
        tasks = Task.query.order_by(Task.due_date).all()
    else:
        tasks = Task.query.filter_by(assigned_to=current_user.id).order_by(Task.due_date).all()
    return render_template('tasks.html', tasks=tasks)

@app.route('/task/create', methods=['GET', 'POST'])
@login_required
def create_task():
    if request.method == 'POST':
        task = Task(
            title=request.form['title'],
            description=request.form.get('description', ''),
            assigned_to=int(request.form['assigned_to']),
            assigned_by=current_user.id,
            due_date=datetime.strptime(request.form['due_date'], '%Y-%m-%d'),
            priority=request.form.get('priority', 'medium'),
            meeting_id=int(request.form['meeting_id']) if request.form.get('meeting_id') else None
        )
        db.session.add(task)
        db.session.commit()
        flash('Task assigned successfully!', 'success')
        return redirect(url_for('view_tasks'))
    
    users = User.query.filter(User.id != current_user.id).all()
    meetings = Meeting.query.all()
    return render_template('create_task.html', users=users, meetings=meetings)

@app.route('/task/<int:id>/update', methods=['POST'])
@login_required
def update_task(id):
    task = Task.query.get_or_404(id)
    
    if task.assigned_to == current_user.id or current_user.role == 'admin':
        status = request.form.get('status')
        if status:
            task.status = status
            db.session.commit()
            flash(f'Task "{task.title}" updated!', 'success')
    else:
        flash('You do not have permission to update this task.', 'danger')
    
    return redirect(url_for('view_tasks'))

# Export routes
@app.route('/reports/export/pdf')
@login_required
def export_pdf():
    if current_user.role != 'admin':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    
    # Create PDF
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
    styles = getSampleStyleSheet()
    story = []
    
    # Title
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=16, spaceAfter=30)
    story.append(Paragraph("Business Administration Report", title_style))
    story.append(Paragraph(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')}", styles['Normal']))
    story.append(Spacer(1, 20))
    
    # Budgets table
    story.append(Paragraph("Budgets Summary", styles['Heading2']))
    budgets = Budget.query.all()
    budget_data = [['Title', 'Department', 'Amount', 'Status', 'Date']]
    for b in budgets:
        budget_data.append([
            b.title, 
            b.department or 'N/A', 
            f'${b.amount:,.2f}', 
            b.status, 
            b.created_at.strftime('%Y-%m-%d')
        ])
    
    budget_table = Table(budget_data)
    budget_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(budget_table)
    story.append(Spacer(1, 20))
    
    # Tasks table
    story.append(Paragraph("Tasks Summary", styles['Heading2']))
    tasks = Task.query.all()
    task_data = [['Title', 'Priority', 'Status', 'Due Date']]
    for t in tasks:
        task_data.append([t.title, t.priority, t.status, t.due_date.strftime('%Y-%m-%d')])
    
    task_table = Table(task_data)
    task_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(task_table)
    
    doc.build(story)
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=True, download_name=f'report_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf', mimetype='application/pdf')

@app.route('/reports/export/excel')
@login_required
def export_excel():
    if current_user.role != 'admin':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    
    wb = Workbook()
    
    # Budgets sheet
    ws_budgets = wb.active
    ws_budgets.title = "Budgets"
    ws_budgets.append(['ID', 'Title', 'Department', 'Amount', 'Status', 'Submitted By', 'Created At'])
    for b in Budget.query.all():
        ws_budgets.append([b.id, b.title, b.department or 'N/A', b.amount, b.status, b.submitted_by, b.created_at.strftime('%Y-%m-%d %H:%M')])
    
    # Tasks sheet
    ws_tasks = wb.create_sheet("Tasks")
    ws_tasks.append(['ID', 'Title', 'Priority', 'Status', 'Assigned To', 'Due Date', 'Created At'])
    for t in Task.query.all():
        ws_tasks.append([t.id, t.title, t.priority, t.status, t.assigned_to, t.due_date.strftime('%Y-%m-%d'), t.created_at.strftime('%Y-%m-%d %H:%M')])
    
    # Meetings sheet
    ws_meetings = wb.create_sheet("Meetings")
    ws_meetings.append(['ID', 'Title', 'Date & Time', 'Duration', 'Meeting Link', 'Location', 'Created By'])
    for m in Meeting.query.all():
        ws_meetings.append([m.id, m.title, m.date_time.strftime('%Y-%m-%d %H:%M'), m.duration, m.meeting_link or 'N/A', m.location or 'N/A', m.created_by])
    
    # Save to buffer
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=True, download_name=f'report_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/budget/<int:id>/export/pdf')
@login_required
def export_budget_pdf(id):
    budget = Budget.query.get_or_404(id)
    
    if current_user.role != 'admin' and budget.submitted_by != current_user.id:
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    # Title
    story.append(Paragraph(f"Budget Request: {budget.title}", styles['Heading1']))
    story.append(Spacer(1, 20))
    
    # Budget details
    details = [
        ['Budget Title:', budget.title],
        ['Department:', budget.department or 'N/A'],
        ['Amount:', f'${budget.amount:,.2f}'],
        ['Status:', budget.status],
        ['Submitted By:', f'User #{budget.submitted_by}'],
        ['Submitted On:', budget.created_at.strftime('%Y-%m-%d %H:%M')],
        ['Description:', budget.description or 'No description provided']
    ]
    
    detail_table = Table(details, colWidths=[2*inch, 4*inch])
    detail_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    story.append(detail_table)
    
    if budget.review_comments:
        story.append(Spacer(1, 20))
        story.append(Paragraph("Review Comments:", styles['Heading2']))
        story.append(Paragraph(budget.review_comments, styles['Normal']))
    
    doc.build(story)
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=True, download_name=f'budget_{budget.id}_{datetime.now().strftime("%Y%m%d")}.pdf', mimetype='application/pdf')

# Reports
@app.route('/reports')
@login_required
def reports():
    if current_user.role != 'admin':
        flash('Only admins can access reports.', 'danger')
        return redirect(url_for('dashboard'))
    
    # Statistics
    total_budgets = Budget.query.count()
    approved_budgets = Budget.query.filter_by(status='approved').all()
    total_approved_amount = sum(b.amount for b in approved_budgets)
    
    total_tasks = Task.query.count()
    completed_tasks = Task.query.filter_by(status='completed').count()
    pending_tasks = Task.query.filter_by(status='pending').count()
    
    total_meetings = Meeting.query.count()
    upcoming_meetings = Meeting.query.filter(Meeting.date_time > datetime.utcnow()).count()
    
    # Budget by department
    from sqlalchemy import func
    departments = db.session.query(Budget.department, func.sum(Budget.amount), func.count(Budget.id))\
        .filter(Budget.status == 'approved')\
        .group_by(Budget.department).all()
    
    return render_template('reports.html',
                         total_budgets=total_budgets,
                         total_approved_amount=total_approved_amount,
                         total_tasks=total_tasks,
                         completed_tasks=completed_tasks,
                         pending_tasks=pending_tasks,
                         total_meetings=total_meetings,
                         upcoming_meetings=upcoming_meetings,
                         departments=departments)

# Calendar view (JSON endpoint for calendar)
@app.route('/api/meetings')
@login_required
def api_meetings():
    meetings = Meeting.query.all()
    events = []
    for meeting in meetings:
        events.append({
            'title': meeting.title,
            'start': meeting.date_time.isoformat(),
            'url': url_for('view_meeting', id=meeting.id)
        })
    return jsonify(events)

# User management (admin only)
@app.route('/users')
@login_required
def view_users():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    users = User.query.all()
    return render_template('users.html', users=users)

@app.route('/user/create', methods=['POST'])
@login_required
def create_user():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    user = User(
        username=request.form['username'],
        email=request.form['email'],
        password=generate_password_hash(request.form['password']),
        role=request.form['role']
    )
    db.session.add(user)
    db.session.commit()
    flash(f'User {user.username} created!', 'success')
    return redirect(url_for('view_users'))

# Auth routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html')

# Simple Google Calendar placeholder (without OAuth for simplicity)
@app.route('/meeting/<int:id>/sync-calendar')
@login_required
def sync_meeting_to_calendar(id):
    meeting = Meeting.query.get_or_404(id)
    flash('Google Calendar sync would be configured here. For now, meeting link is available.', 'info')
    return redirect(url_for('view_meeting', id=id))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_default_admin()
    app.run(debug=True, host='0.0.0.0', port=5000)