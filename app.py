from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import os
import logging
from datetime import datetime
import traceback

# Import your existing classes
from mail_fetcher import MailFetcher
from mail_analyzer import MailAnalyzer
from model_manager import ModelManager
from translation_service import get_translation_service

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Global instances
mail_fetcher = None
mail_analyzer = None
model_manager = None
translation_service = None

def initialize_services():
    """Initialize mail fetcher and analyzer services"""
    global mail_fetcher, mail_analyzer, model_manager, translation_service
    
    try:
        # Initialize mail fetcher
        mail_fetcher = MailFetcher()
        logger.info("Mail fetcher initialized")
        
        # Initialize mail analyzer (for backward compatibility)
        mail_analyzer = MailAnalyzer()
        if mail_analyzer.load_model():
            logger.info("Mail analyzer and model loaded successfully")
        else:
            logger.warning("Failed to load mail analyzer model")
        
        # Initialize model manager
        model_manager = ModelManager()
        
        # Load both models
        models_loaded = model_manager.load_models()
        
        # Initialize translation service
        translation_service = get_translation_service()
        logger.info("Translation service initialized")
        
        if models_loaded:
            status = model_manager.get_model_status()
            logger.info(f"Model manager initialized - Logistic: {status['logistic_loaded']}, SVM: {status['svm_loaded']}")
            return True
        else:
            logger.error("Failed to load any models")
            return False
            
    except Exception as e:
        logger.error(f"Service initialization error: {str(e)}")
        return False

@app.route('/')
def index():
    """Serve the main application page"""
    return render_template('index.html')

@app.route('/presentation')
def presentation():
    """Serve the presentation page"""
    return render_template('presentation.html')

@app.route('/api/fetch-emails', methods=['GET'])
def fetch_emails():
    """Fetch emails with pagination support and model selection"""
    try:
        if not mail_fetcher:
            return jsonify({'error': 'Mail fetcher not initialized'}), 500
        
        # Get pagination parameters
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))
        selected_model = request.args.get('model', 'transformer')  # 'transformer' or 'svm'
        
        # Connect to email server
        if not mail_fetcher.connect():
            return jsonify({'error': 'Failed to connect to email server'}), 500
        
        # Select inbox
        mail_fetcher.select_mailbox('INBOX')
        
        # Search for recent emails
        email_ids = mail_fetcher.search_emails(criteria='ALL', days_back=30)
        
        if not email_ids:
            mail_fetcher.disconnect()
            return jsonify({
                'emails': [], 
                'total_emails': 0,
                'current_page': page,
                'total_pages': 0,
                'per_page': per_page,
                'message': 'No emails found'
            })
        
        # Calculate pagination
        total_emails = len(email_ids)
        total_pages = (total_emails + per_page - 1) // per_page
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        
        # Get emails for current page (reverse order to get latest first)
        reversed_email_ids = email_ids[::-1]
        page_email_ids = reversed_email_ids[start_idx:end_idx]
        
        emails = mail_fetcher.fetch_multiple_emails(page_email_ids, max_emails=per_page)
        
        # Disconnect
        mail_fetcher.disconnect()
        
        # Format emails for frontend and analyze them
        formatted_emails = []
        for i, email in enumerate(emails):
            email_data = {
                'id': start_idx + i,
                'subject': email.get('subject', 'No Subject'),
                'sender': email.get('sender', 'Unknown Sender'),
                'date': email.get('date', 'Unknown Date'),
                'content': email.get('content', ''),
                'preview': email.get('content', '')[:150] + '...' if len(email.get('content', '')) > 150 else email.get('content', '')
            }
            
            # Analyze email with selected model
            if email.get('content'):
                try:
                    analysis_result = None
                    
                    if model_manager:
                        analysis_result = model_manager.analyze_email(
                            email.get('content', ''), 
                            email.get('subject', ''),
                            selected_model
                        )
                    
                    if analysis_result:
                        prediction = analysis_result.get('prediction', 'UNKNOWN')
                        confidence = analysis_result.get('confidence', 0.0)
                        
                        # Label mapping
                        label_mapping = {
                            'LABEL_0': 'Normal', '0': 'Normal', 'normal': 'Normal',
                            'LABEL_1': 'Pazar İhlali', '1': 'Pazar İhlali', 'anormal': 'Anormal',
                            'LABEL_2': 'İhale İhlali', '2': 'İhale İhlali',
                            'LABEL_3': 'Fiyat İhlali', '3': 'Fiyat İhlali',
                            'LABEL_4': 'Bilgi İhlali', '4': 'Bilgi İhlali'
                        }
                        
                        anomaly_type = label_mapping.get(prediction, prediction)
                        is_normal = anomaly_type == 'Normal'
                        
                        # Debug logging
                        logger.info(f"Email {i}: prediction={prediction}, anomaly_type={anomaly_type}, is_normal={is_normal}, confidence={confidence}")
                        
                        email_data.update({
                            'analysis': {
                                'status': anomaly_type,
                                'is_normal': is_normal,
                                'confidence': round(confidence * 100, 2),
                                'prediction': prediction
                            }
                        })
                    else:
                        email_data['analysis'] = {
                            'status': 'Analiz Edilemedi',
                            'is_normal': None,
                            'confidence': 0,
                            'prediction': 'UNKNOWN'
                        }
                except Exception as analysis_error:
                    logger.error(f"Error analyzing email {i}: {str(analysis_error)}")
                    email_data['analysis'] = {
                        'status': 'Analiz Hatası',
                        'is_normal': None,
                        'confidence': 0,
                        'prediction': 'ERROR'
                    }
            else:
                email_data['analysis'] = {
                    'status': 'Analiz Edilemedi',
                    'is_normal': None,
                    'confidence': 0,
                    'prediction': 'NO_ANALYZER'
                }
            
            # Add translation if translation service is available
            if translation_service:
                try:
                    translated_email = translation_service.translate_email_content(email_data)
                    email_data.update(translated_email)
                except Exception as translation_error:
                    logger.error(f"Translation error for email {i}: {str(translation_error)}")
            
            formatted_emails.append(email_data)
        
        return jsonify({
            'emails': formatted_emails,
            'total_emails': total_emails,
            'current_page': page,
            'total_pages': total_pages,
            'per_page': per_page,
            'start_index': start_idx + 1,
            'end_index': min(start_idx + per_page, total_emails),
            'message': f'{len(formatted_emails)} emails fetched and analyzed successfully'
        })
        
    except Exception as e:
        logger.error(f"Error fetching emails: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Failed to fetch emails: {str(e)}'}), 500

@app.route('/api/analyze-email', methods=['POST'])
def analyze_email():
    """Analyze selected email for anomalies"""
    try:
        if not mail_analyzer:
            return jsonify({'error': 'Mail analyzer not initialized'}), 500
        
        data = request.get_json()
        if not data or 'content' not in data:
            return jsonify({'error': 'Email content is required'}), 400
        
        email_content = data['content']
        email_subject = data.get('subject', '')
        
        # Analyze the email
        analysis_result = mail_analyzer.analyze_single_email(email_content, email_subject)
        
        if not analysis_result:
            return jsonify({'error': 'Failed to analyze email'}), 500
        
        # Format result for frontend
        prediction = analysis_result.get('prediction', 'UNKNOWN')
        confidence = analysis_result.get('confidence', 0.0)
        
        # Label mapping for anomaly types
        label_mapping = {
            'LABEL_0': 'Normal',
            'LABEL_1': 'Pazar İhlali', 
            'LABEL_2': 'İhale İhlali',
            'LABEL_3': 'Fiyat İhlali',
            'LABEL_4': 'Bilgi İhlali',
            '0': 'Normal',
            '1': 'Pazar İhlali',
            '2': 'İhale İhlali', 
            '3': 'Fiyat İhlali',
            '4': 'Bilgi İhlali'
        }
        
        # Get anomaly type
        anomaly_type = label_mapping.get(prediction, prediction)
        
        # Determine if email is normal or abnormal
        is_normal = anomaly_type == 'Normal'
        status = anomaly_type
        
        # Confidence level description
        if confidence > 0.8:
            confidence_level = 'Yüksek'
        elif confidence > 0.5:
            confidence_level = 'Orta'
        else:
            confidence_level = 'Düşük'
        
        return jsonify({
            'status': status,
            'is_normal': is_normal,
            'confidence': round(confidence * 100, 2),
            'confidence_level': confidence_level,
            'prediction': prediction,
            'analysis_time': datetime.now().isoformat(),
            'message': f'Email analizi tamamlandı. Sonuç: {status}'
        })
        
    except Exception as e:
        logger.error(f"Error analyzing email: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Failed to analyze email: {str(e)}'}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get application status"""
    model_status = {'logistic_loaded': False, 'svm_loaded': False, 'sentence_transformer_loaded': False}
    if model_manager:
        model_status = model_manager.get_model_status()
    
    return jsonify({
        'status': 'running',
        'services': {
            'mail_fetcher': mail_fetcher is not None,
            'mail_analyzer': mail_analyzer is not None,
            'model_manager': model_manager is not None
        },
        'models': model_status,
        'timestamp': datetime.now().isoformat()
    })

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Initialize services
    if initialize_services():
        logger.info("Starting Flask application...")
        app.run(debug=True, host='0.0.0.0', port=5000)
    else:
        logger.error("Failed to initialize services. Exiting.")
