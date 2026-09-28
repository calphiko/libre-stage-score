import logging


logger = logging.getLogger("uvicorn.error")


def send_email(to_email: str, subject: str, body: str) -> None:
    logger.info("Mail simulation to=%s subject=%s body=%s", to_email, subject, body)

