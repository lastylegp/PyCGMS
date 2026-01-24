"""
Windows Timer Resolution Test

Zeigt den Unterschied zwischen Default (~15ms) und optimierter (~1ms) Timer-Auflösung.
"""
import time
import sys

def measure_sleep_accuracy(target_ms, iterations=100):
    """Misst die tatsächliche Dauer von time.sleep()"""
    errors = []
    for _ in range(iterations):
        start = time.perf_counter()
        time.sleep(target_ms / 1000.0)
        actual = (time.perf_counter() - start) * 1000
        errors.append(actual - target_ms)
    
    avg_error = sum(errors) / len(errors)
    max_error = max(errors)
    min_actual = target_ms + min(errors)
    max_actual = target_ms + max(errors)
    
    return avg_error, min_actual, max_actual

print(f"Platform: {sys.platform}")
print(f"Python: {sys.version}")
print("=" * 60)

# Test OHNE timeBeginPeriod (nur auf Windows relevant)
print("\n1) OHNE Timer-Optimierung (Windows Default):")
print("-" * 40)

for target in [1, 5, 10, 15, 20, 50, 100]:
    avg_err, min_act, max_act = measure_sleep_accuracy(target, 50)
    print(f"   sleep({target:3}ms) → tatsächlich: {min_act:6.1f}ms - {max_act:6.1f}ms (avg error: {avg_err:+.1f}ms)")

# Aktiviere timeBeginPeriod auf Windows
if sys.platform == 'win32':
    print("\n" + "=" * 60)
    print("\n2) MIT timeBeginPeriod(1) - optimierte Auflösung:")
    print("-" * 40)
    
    try:
        import ctypes
        winmm = ctypes.windll.winmm
        result = winmm.timeBeginPeriod(1)
        if result == 0:
            print("   ✓ Timer resolution set to 1ms\n")
        else:
            print(f"   ✗ timeBeginPeriod failed: {result}\n")
        
        for target in [1, 5, 10, 15, 20, 50, 100]:
            avg_err, min_act, max_act = measure_sleep_accuracy(target, 50)
            print(f"   sleep({target:3}ms) → tatsächlich: {min_act:6.1f}ms - {max_act:6.1f}ms (avg error: {avg_err:+.1f}ms)")
        
        # Cleanup
        winmm.timeEndPeriod(1)
        
    except Exception as e:
        print(f"   Error: {e}")
else:
    print("\n(Auf Linux/Mac ist die Timer-Auflösung bereits ~1ms)")

print("\n" + "=" * 60)
print("""
ERKLÄRUNG:
----------
Windows Default Timer: ~15.6ms (64 Hz)
- sleep(1ms)  wird zu ~15ms
- sleep(10ms) wird zu ~15ms  
- sleep(20ms) wird zu ~31ms (2x 15.6ms)

Mit timeBeginPeriod(1): ~1ms
- sleep(1ms)  wird zu ~1-2ms
- sleep(10ms) wird zu ~10-11ms
- sleep(20ms) wird zu ~20-21ms

Für Protokoll-Handshakes (Punter, XModem) ist das kritisch!
""")
