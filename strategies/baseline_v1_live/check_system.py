"""
System Status Checker for Baseline V1 Live Trading

Validates environment setup and OpenAlgo connection before starting trading.

Usage:
    python check_system.py
"""

import os
import sys
from pathlib import Path
import logging

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def check_env_file():
    """Check if .env file exists and has required variables"""
    env_path = Path(__file__).parent / '.env'
    
    if not env_path.exists():
        logger.error(".env file not found")
        logger.info("Create one from .env.example:")
        logger.info("  cp .env.example .env")
        return False
    
    logger.info("[OK] .env file found")
    
    # Load and check variables
    required_vars = ['OPENALGO_API_KEY', 'OPENALGO_HOST', 'OPENALGO_WS_URL']
    
    # Parse .env file lines to reliably detect variables and their values
    with open(env_path) as f:
        lines = f.readlines()

    found = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        key = k.strip()
        # strip quotes and surrounding whitespace from value
        val = v.strip().strip('"').strip("'")
        found[key] = val

    missing = [var for var in required_vars if var not in found]

    # Warn if variables exist but appear to be placeholder/default values
    for var in required_vars:
        if var in found:
            val = found[var]
            if not val or val.lower().startswith('your_') or val.lower() in ('', 'none'):
                logger.warning(f"  {var} not configured (still has default value)")

    if missing:
        logger.error(f"Missing environment variables: {', '.join(missing)}")
        return False

    logger.info("[OK] Environment variables configured")
    return True

def check_openalgo_connection():
    """Check if OpenAlgo is accessible"""
    from baseline_v1_live.config import OPENALGO_HOST
    
    try:
        import requests
        response = requests.get(f"{OPENALGO_HOST}/api/v1/", timeout=5)
        
        if response.status_code == 200:
            logger.info(f"[OK] OpenAlgo is running at {OPENALGO_HOST}")
            return True
        else:
            logger.error(f"OpenAlgo returned status {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        logger.error(f"Cannot connect to OpenAlgo at {OPENALGO_HOST}")
        logger.info("Start OpenAlgo:")
        logger.info("  cd ../openalgo && python app.py")
        return False
    except Exception as e:
        logger.error(f"Error checking OpenAlgo: {e}")
        return False

def check_api_key():
    """Check if API key is valid"""
    from baseline_v1_live.config import OPENALGO_API_KEY, OPENALGO_HOST
    
    if not OPENALGO_API_KEY or OPENALGO_API_KEY == 'your_api_key_here':
        logger.error("API key not configured in .env")
        return False
    
    try:
        from openalgo import api
        client = api(api_key=OPENALGO_API_KEY, host=OPENALGO_HOST)
        
        # Test API call
        response = client.positionbook()
        
        if response.get('status') == 'success':
            logger.info("[OK] API key is valid")
            return True
        else:
            logger.error(f"API key test failed: {response}")
            return False
            
    except Exception as e:
        logger.error(f"API key validation failed: {e}")
        return False

def check_dependencies():
    """Check if required packages are installed"""
    required = ['pandas', 'numpy', 'pytz', 'openalgo']
    
    missing = []
    for package in required:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)
    
    if missing:
        logger.error(f"Missing packages: {', '.join(missing)}")
        logger.info("Install them:")
        logger.info("  pip install -r requirements.txt")
        return False
    
    logger.info("[OK] All dependencies installed")
    return True

def check_directories():
    """Check if required directories exist"""
    dirs = ['logs']
    
    for dir_name in dirs:
        dir_path = Path(__file__).parent / dir_name
        if not dir_path.exists():
            dir_path.mkdir(parents=True)
            logger.info(f"  Created directory: {dir_name}")
    
    logger.info("[OK] Required directories exist")
    return True

def check_broker_connection():
    """Check if broker is logged in via OpenAlgo"""
    from baseline_v1_live.config import OPENALGO_API_KEY, OPENALGO_HOST
    
    try:
        from openalgo import api
        client = api(api_key=OPENALGO_API_KEY, host=OPENALGO_HOST)
        
        # Try to fetch funds
        response = client.funds()
        
        if response.get('status') == 'success':
            data = response.get('data', {})
            available_margin = float(data.get('availablecash', 0))
            logger.info(f"[OK] Broker connected")
            logger.info(f"  Available margin: Rs.{available_margin:,.2f}")
            
            # Check if sufficient margin
            if available_margin < 1000000:  # Rs.10L
                logger.warning(f"  Low margin! Recommended: Rs.10L+")
            
            return True
        else:
            logger.warning("Broker not logged in or API error")
            logger.info("Log in to your broker via OpenAlgo dashboard")
            return False
            
    except Exception as e:
        logger.error(f"Broker connection check failed: {e}")
        return False

def main():
    """Run all checks"""
    print("="*70)
    print("Baseline V1 Live Trading - System Status Check")
    print("="*70)
    print()
    
    checks = [
        ("Environment File", check_env_file),
        ("Dependencies", check_dependencies),
        ("Directories", check_directories),
        ("OpenAlgo Connection", check_openalgo_connection),
        ("API Key", check_api_key),
        ("Broker Connection", check_broker_connection),
    ]
    
    results = []
    
    for name, check_func in checks:
        print(f"Checking {name}...")
        result = check_func()
        results.append((name, result))
        print()
    
    print("="*70)
    print("Summary:")
    print("="*70)
    
    all_passed = True
    for name, result in results:
        status = "[OK] PASS" if result else "[FAIL] FAIL"
        print(f"{status:10} {name}")
        if not result:
            all_passed = False
    
    print()
    
    if all_passed:
        print("[OK] All checks passed! System is ready for trading.")
        print()
        print("Start trading:")
        print("  python baseline_v1_live.py --expiry 26DEC24 --atm 18000")
        print()
        print("Or use quick start script:")
        print("  .\\start.ps1")
    else:
        print("[FAIL] Some checks failed. Please fix the issues above before trading.")
        sys.exit(1)

if __name__ == '__main__':
    main()
