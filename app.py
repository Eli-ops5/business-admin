import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import uuid
import json
import requests
from werkzeug.utils import secure_filename
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-this')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Email configuration (for reminders)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'your-email@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'your-app-password')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME', 'noreply@kenadmin.com')

mail = Mail(app)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='staff')
    calendar_sync = db.Column(db.Boolean, default=False)
    reminder_preference = db.Column(db.String(20), default='email')  # email, none
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    amount = db.Column(db.Float, nullable=False)
    department = db.Column(db.String(100))
    status = db.Column(db.String(20), default='pending')
    submitted_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    reviewed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    review_comments = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Meeting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    date_time = db.Column(db.DateTime, nullable=False)
    duration = db.Column(db.Integer, default=60)
    meeting_link = db.Column(db.String(500))
    location = db.Column(db.String(200))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    reminder_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    assigned_to = db.Column(db.Integer, db.ForeignKey('user.id'))
    assigned_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    due_date = db.Column(db.DateTime, nullable=False)
    priority = db.Column(db.String(20), default='medium')
    status = db.Column(db.String(20), default='pending')
    meeting_id = db.Column(db.Integer, db.ForeignKey('meeting.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class MeetingAttendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey('meeting.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    status = db.Column(db.String(20), default='invited')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class CalendarEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey('meeting.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    event_id = db.Column(db.String(200))
    calendar_type = db.Column(db.String(50), default='ical')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Email reminder function
def send_meeting_reminder(meeting, user):
    try:
        msg = Message(
            f'Reminder: {meeting.title} - KEN Admin',
            recipients=[user.email]
        )
        msg.body = f"""
Hello {user.username},

This is a reminder for your upcoming meeting:

Meeting: {meeting.title}
Date: {meeting.date_time.strftime('%Y-%m-%d')}
Time: {meeting.date_time.strftime('%H:%M')}
Duration: {meeting.duration} minutes

Join link: {meeting.meeting_link or 'No link provided'}

You can view full details at: https://business-admin-1hpp.onrender.com/meeting/{meeting.id}

Best regards,
KEN Admin Team
"""
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

# Scheduled task to send reminders
scheduler = BackgroundScheduler()

def check_and_send_reminders():
    with app.app_context():
        now = datetime.utcnow()
        # Send reminders 1 hour before meeting
        reminder_time = now + timedelta(hours=1)
        
        meetings = Meeting.query.filter(
            Meeting.date_time <= reminder_time,
            Meeting.date_time > now,
            Meeting.reminder_sent == False
        ).all()
        
        for meeting in meetings:
            attendees = MeetingAttendance.query.filter_by(meeting_id=meeting.id).all()
            for attendee in attendees:
                user = User.query.get(attendee.user_id)
                if user and user.reminder_preference == 'email':
                    if send_meeting_reminder(meeting, user):
                        pass
            meeting.reminder_sent = True
            db.session.commit()

# Start scheduler
scheduler.add_job(func=check_and_send_reminders, trigger="interval", minutes=30)
scheduler.start()

# Routes
@app.route('/')
@login_required
def dashboard():
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

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.email = request.form.get('email', current_user.email)
        current_user.reminder_preference = request.form.get('reminder_preference', 'email')
        db.session.commit()
        flash('Profile updated!', 'success')
        return redirect(url_for('profile'))
    return render_template('profile.html')

@app.route('/calendar-sync')
@login_required
def calendar_sync():
    """Generate iCal file for user's meetings"""
    meetings = Meeting.query.join(MeetingAttendance).filter(
        MeetingAttendance.user_id == current_user.id,
        Meeting.date_time > datetime.utcnow()
    ).all()
    
    cal_data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//KEN Admin//Calendar//EN
"""
    for meeting in meetings:
        cal_data += f"""
BEGIN:VEVENT
SUMMARY:{meeting.title}
DTSTART:{meeting.date_time.strftime('%Y%m%dT%H%M%S')}
DTEND:{(meeting.date_time + timedelta(minutes=meeting.duration)).strftime('%Y%m%dT%H%M%S')}
DESCRIPTION:{meeting.description or ''}
LOCATION:{meeting.location or meeting.meeting_link or ''}
END:VEVENT"""
    
    cal_data += "\nEND:VCALENDAR"
    
    return send_file(
        BytesIO(cal_data.encode()),
        mimetype='text/calendar',
        as_attachment=True,
        download_name='ken_admin_calendar.ics'
    )

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
        
        # Add creator as attendee
        attendance = MeetingAttendance(meeting_id=meeting.id, user_id=current_user.id, status='confirmed')
        db.session.add(attendance)
        
        # Add all staff as invited
        staff_users = User.query.filter(User.role == 'staff').all()
        for user in staff_users:
            if user.id != current_user.id:
                attendance = MeetingAttendance(meeting_id=meeting.id, user_id=user.id, status='invited')
                db.session.add(attendance)
        
        db.session.commit()
        flash('Meeting scheduled successfully! Reminders will be sent 1 hour before.', 'success')
        return redirect(url_for('view_meetings'))
    return render_template('create_meeting.html')

@app.route('/meeting/<int:id>')
@login_required
def view_meeting(id):
    meeting = Meeting.query.get_or_404(id)
    attendees = MeetingAttendance.query.filter_by(meeting_id=id).all()
    return render_template('view_meeting.html', meeting=meeting, attendees=attendees)

@app.route('/meeting/<int:id>/ical')
@login_required
def download_ical(id):
    meeting = Meeting.query.get_or_404(id)
    cal_data = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//KEN Admin//Meeting//EN
BEGIN:VEVENT
SUMMARY:{meeting.title}
DTSTART:{meeting.date_time.strftime('%Y%m%dT%H%M%S')}
DTEND:{(meeting.date_time + timedelta(minutes=meeting.duration)).strftime('%Y%m%dT%H%M%S')}
DESCRIPTION:{meeting.description or ''}
LOCATION:{meeting.location or meeting.meeting_link or ''}
END:VEVENT
END:VCALENDAR"""
    
    return send_file(
        BytesIO(cal_data.encode()),
        mimetype='text/calendar',
        as_attachment=True,
        download_name=f'{meeting.title}.ics'
    )

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
            submitted_by=current_user.id
        )
        db.session.add(budget)
        db.session.commit()
        flash('Budget submitted successfully!', 'success')
        return redirect(url_for('view_budgets'))
    return render_template('create_budget.html')

@app.route('/budget/<int:id>')
@login_required
def view_budget(id):
    budget = Budget.query.get_or_404(id)
    return render_template('view_budget.html', budget=budget)

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
    elif action == 'reject':
        budget.status = 'rejected'
        flash(f'Budget "{budget.title}" rejected.', 'warning')
    db.session.commit()
    return redirect(url_for('view_budgets'))

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
            priority=request.form.get('priority', 'medium')
        )
        db.session.add(task)
        db.session.commit()
        flash('Task assigned successfully!', 'success')
        return redirect(url_for('view_tasks'))
    users = User.query.filter(User.id != current_user.id).all()
    return render_template('create_task.html', users=users)

@app.route('/task/<int:id>/update', methods=['POST'])
@login_required
def update_task(id):
    task = Task.query.get_or_404(id)
    if task.assigned_to == current_user.id or current_user.role == 'admin':
        task.status = request.form.get('status')
        db.session.commit()
        flash('Task updated!', 'success')
    return redirect(url_for('view_tasks'))

@app.route('/reports')
@login_required
def reports():
    if current_user.role != 'admin':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    total_budgets = Budget.query.count()
    approved_budgets = Budget.query.filter_by(status='approved').all()
    total_approved_amount = sum(b.amount for b in approved_budgets)
    total_tasks = Task.query.count()
    completed_tasks = Task.query.filter_by(status='completed').count()
    pending_tasks = Task.query.filter_by(status='pending').count()
    return render_template('reports.html', 
                         total_budgets=total_budgets,
                         total_approved_amount=total_approved_amount,
                         total_tasks=total_tasks,
                         completed_tasks=completed_tasks,
                         pending_tasks=pending_tasks)

@app.route('/users')
@login_required
def view_users():
    if current_user.role != 'admin':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    users = User.query.all()
    return render_template('users.html', users=users)

@app.route('/user/create', methods=['POST'])
@login_required
def create_user():
    if current_user.role != 'admin':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    user = User(
        username=request.form['username'],
        email=request.form['email'],
        password=generate_password_hash(request.form['password']),
        role=request.form['role'],
        reminder_preference='email'
    )
    db.session.add(user)
    db.session.commit()
    flash(f'User {user.username} created!', 'success')
    return redirect(url_for('view_users'))

# Initialize database
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', email='admin@business.com', 
                    password=generate_password_hash('admin123'), role='admin', reminder_preference='email')
        db.session.add(admin)
        staff = User(username='staff', email='staff@business.com', 
                    password=generate_password_hash('staff123'), role='staff', reminder_preference='email')
        db.session.add(staff)
        db.session.commit()
        print("✅ KEN Admin initialized with default users")

@app.route('/calendar-sync')
@login_required
def calendar_sync():
    """Generate iCal file for user's meetings"""
    from datetime import timedelta
    from io import BytesIO
    
    # Get all meetings for this user
    meetings = Meeting.query.join(MeetingAttendance).filter(
        MeetingAttendance.user_id == current_user.id,
        Meeting.date_time > datetime.utcnow()
    ).all()
    
    cal_data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//KEN Admin//Calendar//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
"""
    
    for meeting in meetings:
        dtstart = meeting.date_time.strftime('%Y%m%dT%H%M%SZ')
        dtend = (meeting.date_time + timedelta(minutes=meeting.duration)).strftime('%Y%m%dT%H%M%SZ')
        
        cal_data += f"""
BEGIN:VEVENT
UID:{meeting.id}@kenadmin.com
DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{meeting.title}
DESCRIPTION:{meeting.description or ''}
LOCATION:{meeting.location or meeting.meeting_link or ''}
STATUS:CONFIRMED
SEQUENCE:0
END:VEVENT"""
    
    cal_data += "\nEND:VCALENDAR"
    
    return send_file(
        BytesIO(cal_data.encode('utf-8')),
        mimetype='text/calendar',
        as_attachment=True,
        download_name='ken_admin_calendar.ics'
    )


@app.route('/meeting/<int:id>/ical')
@login_required
def download_ical(id):
    """Download single meeting as iCal file"""
    from datetime import timedelta
    from io import BytesIO
    
    meeting = Meeting.query.get_or_404(id)
    
    # Check if user is invited or creator
    attendance = MeetingAttendance.query.filter_by(
        meeting_id=meeting.id, 
        user_id=current_user.id
    ).first()
    
    if not attendance and meeting.created_by != current_user.id and current_user.role != 'admin':
        flash('You are not invited to this meeting.', 'danger')
        return redirect(url_for('view_meetings'))
    
    dtstart = meeting.date_time.strftime('%Y%m%dT%H%M%SZ')
    dtend = (meeting.date_time + timedelta(minutes=meeting.duration)).strftime('%Y%m%dT%H%M%SZ')
    
    cal_data = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//KEN Admin//Meeting//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
UID:{meeting.id}@kenadmin.com
DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{meeting.title}
DESCRIPTION:{meeting.description or ''}
LOCATION:{meeting.location or meeting.meeting_link or ''}
STATUS:CONFIRMED
SEQUENCE:0
END:VEVENT
END:VCALENDAR"""
    
    return send_file(
        BytesIO(cal_data.encode('utf-8')),
        mimetype='text/calendar',
        as_attachment=True,
        download_name=f'{meeting.title.replace(" ", "_")}.ics'
    )

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
