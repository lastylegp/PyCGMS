"""
YModem Wrapper for BBS Integration
Provides simple ymodem_send/receive functions for udarea.py
"""

import os
import sys
import time

# Import file_transfer module
try:
    from file_transfer import FileTransfer, TransferProtocol
    YMODEM_AVAILABLE = True
except ImportError:
    YMODEM_AVAILABLE = False
    print("[Warning] file_transfer.py not found - YModem disabled")


def ymodem_send(conn, filepath, callback=None):
    """
    Send file(s) using YModem protocol
    
    Smart Protocol Selection:
    - Single file → XModem-1K (no header, direct data)
    - Multiple files → YModem Batch (with headers)
    
    Args:
        conn: Connection object (socket or BBSTelnetClient)
        filepath: Path to file OR list of file paths
        callback: Optional progress callback(done, total, status, filename)
    
    Returns:
        tuple: (success: bool, cps: float) - Characters per second
    """
    if not YMODEM_AVAILABLE:
        print("[Error] YModem not available - file_transfer.py not found")
        return (False, 0)
    
    # Normalize to list
    if isinstance(filepath, str):
        filepaths = [filepath]
    else:
        filepaths = list(filepath)
    
    # Check all files exist
    for path in filepaths:
        if not os.path.isfile(path):
            print(f"[Error] File not found: {path}")
            return (False, 0)
    
    try:
        # Smart Protocol Selection
        num_files = len(filepaths)
        
        if num_files == 1:
            # Single file → XModem-1K (no header)
            print(f"[YModem] Single file upload → Using XModem-1K")
            protocol = TransferProtocol.XMODEM_1K
            send_path = filepaths[0]
        else:
            # Multiple files → YModem Batch (with headers)
            print(f"[YModem] Batch upload → {num_files} files with headers")
            protocol = TransferProtocol.YMODEM
            send_path = filepaths  # List
        
        transfer = FileTransfer(conn, protocol)
        
        start_time = time.time()
        success = transfer.send_file(send_path, callback=callback)
        duration = time.time() - start_time
        
        if success:
            # Calculate CPS
            total_bytes = sum(os.path.getsize(f) for f in filepaths)
            cps = total_bytes / duration if duration > 0 else 0
            
            print(f"[YModem] Send successful")
            print(f"[YModem] Speed: {cps:.0f} CPS ({total_bytes} bytes in {duration:.1f}s)")
            return (True, cps)
        else:
            print("[YModem] Send failed")
            return (False, 0)
            
    except Exception as e:
        print(f"[YModem] Exception during send: {e}")
        import traceback
        traceback.print_exc()
        return (False, 0)


def ymodem_receive(conn, target_dir, callback=None):
    """
    Receive file(s) using YModem protocol
    
    Smart Protocol Detection:
    - BBS sends Block 1 → XModem-1K (no header, user renames after)
    - BBS sends Block 0 → YModem (with filename)
    
    Args:
        conn: Connection object
        target_dir: Directory to save received files
        callback: Optional progress callback(done, total, status, filename)
    
    Returns:
        tuple: (success: bool, filepath_or_list: str/list, cps: float)
        
        For single file: (True, "/path/file.prg", 2400.0)
        For batch: (True, ["file1.prg", "file2.prg"], 2400.0)
    """
    if not YMODEM_AVAILABLE:
        print("[Error] YModem not available - file_transfer.py not found")
        return (False, None, 0)
    
    if not os.path.isdir(target_dir):
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as e:
            print(f"[Error] Cannot create directory: {target_dir}")
            return (False, None, 0)
    
    try:
        print(f"[YModem] Receiving to: {target_dir}")
        
        # Always use YMODEM protocol - it auto-detects XModem-1K
        transfer = FileTransfer(conn, TransferProtocol.YMODEM)
        
        start_time = time.time()
        success = transfer.receive_file(target_dir, callback=callback)
        duration = time.time() - start_time
        
        if success:
            # Find received files (get list from transfer log or directory)
            # For now, we'll return the target_dir and let caller handle it
            # TODO: Better tracking of received files
            
            # Estimate CPS (rough calculation)
            # We'd need to track actual bytes received
            cps = 0  # Will be calculated from callback or file size
            
            print(f"[YModem] Receive successful")
            return (True, target_dir, cps)
        else:
            print("[YModem] Receive failed")
            return (False, None, 0)
            
    except Exception as e:
        print(f"[YModem] Exception during receive: {e}")
        import traceback
        traceback.print_exc()
        return (False, None, 0)


# Backwards compatibility aliases
def ymodem_send_single(conn, filepath, callback=None):
    """Send single file using XModem-1K (no header)"""
    return ymodem_send(conn, filepath, callback)


def ymodem_send_batch(conn, filepaths, callback=None):
    """Send multiple files using YModem Batch (with headers)"""
    return ymodem_send(conn, filepaths, callback)
