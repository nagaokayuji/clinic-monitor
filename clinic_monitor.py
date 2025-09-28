#!/usr/bin/env python3
"""
Script to monitor clinic booking cancellations and notify via Slack
"""

import os
import time
import asyncio
import requests
from datetime import datetime
from playwright.async_api import async_playwright
import logging
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Logging configuration
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration from environment variables
CLINIC_URL = os.getenv('CLINIC_URL', '')
SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL', '')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL',
                               '600'))  # Check interval in seconds
NOTIFICATION_INTERVAL = int(os.getenv('NOTIFICATION_INTERVAL',
                                      '1800'))  # 30 minutes in seconds


def validate_environment():
    """
    Validate that required environment variables are set
    """
    if not SLACK_WEBHOOK_URL:
        raise ValueError("SLACK_WEBHOOK_URL environment variable is required")

    # Validate Slack webhook URL format
    try:
        parsed = urlparse(SLACK_WEBHOOK_URL)
        if not all([parsed.scheme, parsed.netloc]):
            raise ValueError("SLACK_WEBHOOK_URL must be a valid URL")
        if not parsed.netloc.endswith('slack.com'):
            raise ValueError("SLACK_WEBHOOK_URL must be a Slack webhook URL")
    except Exception as e:
        raise ValueError(f"Invalid SLACK_WEBHOOK_URL: {e}")

    if not CLINIC_URL:
        raise ValueError("CLINIC_URL environment variable is required")

    # Validate clinic URL format
    try:
        parsed = urlparse(CLINIC_URL)
        if not all([parsed.scheme, parsed.netloc]):
            raise ValueError("CLINIC_URL must be a valid URL")
    except Exception as e:
        raise ValueError(f"Invalid CLINIC_URL: {e}")

    logger.info("Environment variables validated successfully")


async def check_availability(max_retries=3):
    """
    Check availability on the booking site
    Returns: bool - Whether there are available slots for today
    """
    for attempt in range(max_retries):
        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                # Set longer timeout for navigation
                page.set_default_timeout(30000)

                try:
                    # Open the page with retry logic
                    await page.goto(CLINIC_URL,
                                    wait_until='networkidle',
                                    timeout=30000)
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Navigation failed on attempt {attempt + 1}: {e}")
                        await browser.close()
                        await asyncio.sleep(5)  # Wait before retry
                        continue
                    else:
                        raise

                # Wait a bit for content to fully load
                await page.wait_for_timeout(3000)

                # Get elements containing day-flex-col
                day_flex_col = await page.query_selector_all('.day-flex-col')

                if not day_flex_col:
                    logger.warning("day-flex-col elements not found")
                    return False

                # Get the first day-flex-col (today's appointments)
                today_column = day_flex_col[0]

                # Look for day-cell elements with capacity-enough or capacity-few class
                available_slots = await today_column.query_selector_all(
                    '.day-cell.capacity-enough, .day-cell.capacity-few')

                # Check tomorrow's slots for logging
                tomorrow_cols = day_flex_col[1] if len(
                    day_flex_col) > 1 else None
                if tomorrow_cols:
                    tomorrow_slots = await tomorrow_cols.query_selector_all(
                        '.day-cell.capacity-enough, .day-cell.capacity-few')
                    if tomorrow_slots:
                        logger.info(
                            f"Found {len(tomorrow_slots)} available slots for tomorrow's appointments"
                        )

                if available_slots:
                    logger.info(
                        f"Found {len(available_slots)} available slots for today's appointments"
                    )
                    return True
                else:
                    logger.info("No available slots for today's appointments")
                    return False

        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"Check availability failed on attempt {attempt + 1}: {e}")
                await asyncio.sleep(10)  # Wait before retry
            else:
                logger.error(
                    f"Error occurred while checking the page after {max_retries} attempts: {e}"
                )
                return False
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception as e:
                    logger.warning(f"Error closing browser: {e}")

    return False


def send_slack_notification():
    """
    Send notification to Slack
    """
    message = {
        "text":
        "🏥 Clinic appointment slots are now available!",
        "attachments": [{
            "color":
            "good",
            "fields": [{
                "title": "Check Time",
                "value": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "short": True
            }, {
                "title": "Booking Site",
                "value": f"<{CLINIC_URL}|Book here>",
                "short": False
            }]
        }]
    }

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=message)
        if response.status_code == 200:
            logger.info("Slack notification sent successfully")
        else:
            logger.error(
                f"Failed to send Slack notification: {response.status_code}")
    except Exception as e:
        logger.error(f"Error occurred while sending Slack notification: {e}")


async def monitor_bookings():
    """
    Main monitoring loop
    """
    logger.info("Starting booking monitoring...")
    last_notification_time = 0

    while True:
        try:
            current_time = time.time()

            # Check availability
            has_availability = await check_availability()

            if has_availability:
                # Only notify if more than specified interval has passed since last notification
                if current_time - last_notification_time > NOTIFICATION_INTERVAL:
                    send_slack_notification()
                    last_notification_time = current_time
                else:
                    logger.info(
                        "Slots are available but skipping notification due to interval"
                    )

            # Wait until next check
            await asyncio.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Stopping monitoring")
            break
        except Exception as e:
            logger.error(f"Unexpected error occurred: {e}")
            await asyncio.sleep(60)  # Wait 1 minute before retry on error


def main():
    """
    Main function
    """
    try:
        # Validate environment variables
        validate_environment()

        # Start monitoring asynchronously
        asyncio.run(monitor_bookings())

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        print(f"Error: {e}")
        print("\nRequired environment variables:")
        print("- SLACK_WEBHOOK_URL: Your Slack webhook URL")
        print("- CLINIC_URL: Clinic booking URL (uses default if not set)")
        print("\nOptional environment variables:")
        print("- CHECK_INTERVAL: Check interval in seconds (default: 600)")
        print(
            "- NOTIFICATION_INTERVAL: Minimum time between notifications in seconds (default: 1800)"
        )
        return 1
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
