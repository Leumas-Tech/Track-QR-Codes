from flask import Flask, request, redirect, render_template, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import qrcode
from PIL import Image, ImageDraw
import os
import json
import cloudinary
import cloudinary.uploader

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///qr_logs.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_secret_key'
db = SQLAlchemy(app)

# Load user-specific configuration
with open('config.json') as config_file:
    config = json.load(config_file)

CLOUDINARY_CONFIG = config.get('cloudinary', {})

class QRLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    qr_code_id = db.Column(db.String(120), nullable=False)
    original_url = db.Column(db.String(2048), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_agent = db.Column(db.String(200), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate', methods=['GET', 'POST'])
def generate_qr():
    if request.method == 'POST':
        url = request.form['url']
        bg_color = request.form.get('bg_color', 'white')
        if request.form.get('transparent_bg'):
            bg_color = None
        border_width = int(request.form.get('border_width', 0))
        border_radius = int(request.form.get('border_radius', 0))
        border_color = request.form.get('border_color', 'black')
        padding = int(request.form.get('padding', 0))
        logo = request.files.get('logo')
        save_to_cloudinary = request.form.get('save_to_cloudinary') == 'true'

        if not url:
            flash('Please enter a URL', 'error')
            return redirect(url_for('generate_qr'))
        
        # Create a QR code ID from the URL
        qr_code_id = url.split("//")[1].split('.')[0]

        # Create QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=padding // 10,  # Adjust border based on padding
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color=bg_color or "white")

        # Add logo if provided
        if logo:
            logo = Image.open(logo)
            logo = logo.resize((50, 50), Image.LANCZOS)
            img = img.convert("RGBA")
            logo_pos = ((img.size[0] - logo.size[0]) // 2, (img.size[1] - logo.size[1]) // 2)
            img.paste(logo, logo_pos, logo)

        # Add border if specified
        if border_width > 0:
            img = img.convert("RGBA")
            border_color_rgb = Image.new("RGBA", img.size, border_color)
            img_with_border = Image.new("RGBA", (img.size[0] + 2 * border_width, img.size[1] + 2 * border_width), border_color)
            img_with_border.paste(img, (border_width, border_width))
            img = img_with_border

            # Add border radius if specified
            if border_radius > 0:
                mask = Image.new("L", img.size, 0)
                draw = ImageDraw.Draw(mask)
                draw.rounded_rectangle((0, 0) + img.size, border_radius, fill=255)
                img.putalpha(mask)

        # Save the image locally
        img_path = os.path.join('static', 'qrcodes', f'{qr_code_id}.png')
        img.save(img_path)

        # Save the QR log with the original URL
        new_log = QRLog(qr_code_id=qr_code_id, original_url=url)
        db.session.add(new_log)
        db.session.commit()

        # Upload to Cloudinary if required
        cloudinary_url = None
        if save_to_cloudinary:
            if not CLOUDINARY_CONFIG:
                flash('Cloudinary configuration not found. Please provide Cloudinary configuration.', 'error')
                return redirect(url_for('generate_qr'))

            cloudinary.config(
                cloud_name=CLOUDINARY_CONFIG['cloud_name'],
                api_key=CLOUDINARY_CONFIG['api_key'],
                api_secret=CLOUDINARY_CONFIG['api_secret']
            )
            response = cloudinary.uploader.upload(img_path, folder="qrcodes")
            cloudinary_url = response['secure_url']

        flash('QR Code generated successfully!', 'success')
        return render_template('generate_qr.html', qr_code_img=f'qrcodes/{qr_code_id}.png', cloudinary_url=cloudinary_url)
    
    return render_template('generate_qr.html', cloudinary_config=bool(CLOUDINARY_CONFIG))

@app.route('/scan/<qr_code_id>')
def scan_qr_code(qr_code_id):
    user_agent = request.headers.get('User-Agent')
    ip_address = request.remote_addr

    log_entry = QRLog.query.filter_by(qr_code_id=qr_code_id).first()
    if not log_entry:
        return redirect('https://leumas.tech')  # Default URL if not found

    new_log = QRLog(qr_code_id=qr_code_id, user_agent=user_agent, ip_address=ip_address, original_url=log_entry.original_url)
    db.session.add(new_log)
    db.session.commit()

    return redirect(log_entry.original_url)

@app.route('/api/logs', methods=['GET'])
def get_logs():
    logs = QRLog.query.order_by(QRLog.timestamp.desc()).all()
    log_list = [{
        "id": log.id,
        "qr_code_id": log.qr_code_id,
        "timestamp": log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        "user_agent": log.user_agent,
        "ip_address": log.ip_address
    } for log in logs]
    return jsonify(log_list)

@app.route('/api/stats', methods=['GET'])
def get_stats():
    logs = QRLog.query.all()
    data = {}
    
    for log in logs:
        date = log.timestamp.date().isoformat()
        if date not in data:
            data[date] = 0
        data[date] += 1

    return jsonify(data)

@app.route('/qrcodes')
def qr_codes():
    qr_codes = os.listdir('static/qrcodes')
    return render_template('qr_codes.html', qr_codes=qr_codes)

@app.route('/qrstats/<qr_code_id>')
def qr_stats(qr_code_id):
    logs = QRLog.query.filter_by(qr_code_id=qr_code_id).order_by(QRLog.timestamp.desc()).all()
    return render_template('qr_stats.html', qr_code_id=qr_code_id, logs=logs)

@app.route('/charts')
def charts():
    return render_template('charts.html')

@app.route('/edit_qr/<qr_code_id>', methods=['GET', 'POST'])
def edit_qr(qr_code_id):
    if request.method == 'POST':
        new_id = request.form['new_id']
        old_path = os.path.join('static', 'qrcodes', f'{qr_code_id}.png')
        new_path = os.path.join('static', 'qrcodes', f'{new_id}.png')
        os.rename(old_path, new_path)
        
        # Update logs
        logs = QRLog.query.filter_by(qr_code_id=qr_code_id).all()
        for log in logs:
            log.qr_code_id = new_id
            db.session.commit()
        
        flash('QR Code updated successfully!', 'success')
        return redirect(url_for('qr_codes'))
    return render_template('edit_qr.html', qr_code_id=qr_code_id)

@app.route('/delete_qr/<qr_code_id>', methods=['POST'])
def delete_qr(qr_code_id):
    path = os.path.join('static', 'qrcodes', f'{qr_code_id}.png')
    if os.path.exists(path):
        os.remove(path)
    
    # Delete logs
    QRLog.query.filter_by(qr_code_id=qr_code_id).delete()
    db.session.commit()
    
    flash('QR Code deleted successfully!', 'success')
    return redirect(url_for('qr_codes'))

@app.route('/export_qr/<qr_code_id>')
def export_qr(qr_code_id):
    path = os.path.join('static', 'qrcodes', f'{qr_code_id}.png')
    return send_file(path, as_attachment=True)

@app.route('/import_qr', methods=['GET', 'POST'])
def import_qr():
    if request.method == 'POST':
        qr_code_id = request.form['qr_code_id']
        file = request.files['file']
        
        if file and qr_code_id:
            filename = f'{qr_code_id}.png'
            filepath = os.path.join('static', 'qrcodes', filename)
            file.save(filepath)
            
            flash('QR Code imported successfully!', 'success')
            return redirect(url_for('qr_codes'))
        else:
            flash('Please provide a QR Code ID and select a file.', 'error')
    
    return render_template('import_qr.html')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0')
