# src/logger.py
import logging
from pathlib import Path
from datetime import datetime

def setup_logger(name: str = "app_logger", level: int = logging.INFO) -> logging.Logger:
    """
    Cria um logger 'name' com handler de arquivo e console.
    O arquivo de log é salvo sempre dentro da pasta 'src/logs/'.
    """

    
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True) # Garante que a pasta 'logs' exista

    # 2) Instancia logger específico (não o root)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False # evita duplicar no root

    # 3) Só adiciona handlers se ainda não houver (para evitar duplicatas em chamadas repetidas)
    if not logger.handlers:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{name}_{ts}.log"
        file_path = log_dir / file_name

        # Formato da mensagem do log
        fmt = "[%(asctime)s] %(levelname)-8s | %(name)s | %(message)s"
        formatter = logging.Formatter(fmt, "%Y-%m-%d %H:%M:%S")

        # Handler para o arquivo
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        # Handler para o console (saída padrão)
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)

        # Loga onde o arquivo ficou para você conferir
        logger.info("Log file created at: %s", file_path.resolve())

    return logger