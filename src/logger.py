import logging
import logging.config
import os
from datetime import datetime
from pathlib import Path

def setup_logger(log_level=logging.INFO, log_file=None):
    """
    Set up logging configuration for the entire application.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file name. If None, logs to console only.
                 The file will be created in the 'logs' folder at project root.
    """
    
    # diretorio raiz
    project_root = Path(__file__).parent.parent
    logs_dir = project_root / "logs"
    
    # cria diretorio de logs
    logs_dir.mkdir(exist_ok=True)
    
    # If log_file is provided, create full path
    if log_file:
        log_file_path = logs_dir / log_file
    else:
        log_file_path = None
    
    # formato
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # configuracao
    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard': {
                'format': log_format,
                'datefmt': date_format
            },
            'detailed': {
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
                'datefmt': date_format
            }
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'level': log_level,
                'formatter': 'standard',
                'stream': 'ext://sys.stdout'
            }
        },
        'loggers': {
            '': {  # Root logger
                'handlers': ['console'],
                'level': log_level,
                'propagate': False
            }
        }
    }
    
    # Add file handler if log_file is specified
    if log_file_path:
        logging_config['handlers']['file'] = {
            'class': 'logging.handlers.RotatingFileHandler',
            'level': log_level,
            'formatter': 'detailed',
            'filename': str(log_file_path),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5,
            'encoding': 'utf8'
        }
        logging_config['loggers']['']['handlers'].append('file')
    
    logging.config.dictConfig(logging_config)
    
    # Log the startup
    logger = logging.getLogger(__name__)
    logger.info(f"Logging system initialized. Log directory: {logs_dir}")
    if log_file_path:
        logger.info(f"Log file: {log_file_path}")
    
    return logger

def get_log_filename():
    """Generate a log filename"""
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"aportes_otimizacao_{now}.log"

def get_project_root():
    """Get the project root directory"""
    return Path(__file__).parent.parent