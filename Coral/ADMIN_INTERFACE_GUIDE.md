# Admin Interface & Enrollment System

This guide covers admin interface and enrollment flow for Coral Face Recognition System.

## Overview

Main parts:

1. **EnrollmentController** (`enrollment_controller.py`) - Handles new user enrollment
2. **AdminInterface** (`admin_interface.py`) - CLI for admin ops

## Components

### EnrollmentController

Handles enrollment flow for new users:

```python
from enrollment_controller import EnrollmentController

controller = EnrollmentController(mock_mode=False)

# Start enrollment for new user
session_id = controller.start_enrollment(
    user_id="user_001",
    username="John Doe",
    metadata={"role": "owner", "email": "john@example.com"}
)

# Process frames to capture embeddings
for _ in range(60):  # ~4 seconds at 15 FPS
    status = controller.capture_enrollment_frame()
    print(f"Embeddings: {status['embeddings_captured']}/{status['embeddings_target']}")
    
    if status['state'] == 'success':
        print("Enrollment complete!")
        break

# Or cancel enrollment
controller.cancel_enrollment()
```

**Key Features:**

- **Automatic completion**: Captures embeddings until target reached 
- **Real-time feedback**: Shows capture progress
- **Rollback on failure**: Removes user record if enrollment fails
- **Timeout protection**: Times out after 120 seconds (configurable)
- **Multiple embeddings**: Captures multiple angles for better recognition

### AdminInterface

CLI menu for admin ops:

```bash
# Run admin interface (with real camera)
python -m coral.admin_interface

# Run with mock mode (testing without hardware)
python admin_interface.py --mock
```

## Workflows

### 1. Enroll New User

```
Main Menu → [1] Enroll new user
  ↓
Enter user ID and name
  ↓
(Optional) Add metadata (role, email)
  ↓
Confirm enrollment
  ↓
Camera initializes and frame capture starts
  ↓
Real-time feedback as faces are detected and embeddings captured
  ↓
SUCCESS: User enrolled with N embeddings
```

**What happens:**

1. Admin enters user info (ID, name, optional metadata)
2. System creates user record
3. Camera opens and starts capture
4. For each frame:
  - Face detector finds face
  - Face recognizer generates embedding
  - Embedding is encrypted and stored
  - Progress shows in real time
5. Continue until target embeddings reached or timeout
6. Success: user enrolled with encrypted face data
7. Failure: user record rolls back/deletes

**Real-time feedback:**

```
[Frames: 45] [Embeddings: 3/5] 
[Frames: 50] [Embeddings: 4/5] 
[Frames: 55] [Embeddings: 5/5] ✓ Enrollment complete!
```

### 2. Test Authentication

```
Main Menu → [2] Test authentication
  ↓
Display enrolled users
  ↓
Confirm to start auth test
  ↓
Camera initializes and frame capture starts
  ↓
Real-time feedback as system:
  - Detects face
  - Recognizes user
  - Challenges with liveness (head turn, blink, etc)
  ↓
SUCCESS: User authenticated with confidence score
```

**What happens:**

1. System lists enrolled users
2. Camera opens and starts capture
3. Full auth pipeline runs:
  - **Face Detection**: Finds face in frame
  - **Face Recognition**: Matches stored embeddings
  - **Liveness Challenge**: Random challenge (turn head, blink, etc.)
  - **Result**: Success with confidence or failure with error
4. Real-time feedback shows:
   - Current recognized user (if any)
   - Confidence score
   - Liveness challenge type
   - Errors (face not detected, low confidence, liveness timeout, etc.)

**Real-time feedback:**

```
[User: user_001] [Confidence: x%]
[User: user_001] [Confidence: x%] [Challenge: Turn head left]
✓ AUTHENTICATION SUCCESS!
  User: user_001
  Confidence: x%
  Liveness: Verified
```

### 3. Remove User

```
Main Menu → [3] Remove user
  ↓
Display list of enrolled users
  ↓
Enter user ID to remove
  ↓
Show warning with user info
  ↓
Confirm deletion (type 'DELETE')
  ↓
User and all embeddings securely deleted
```

**What happens:**

1. System lists enrolled users with embedding counts
2. Admin enters user ID to remove
3. System shows permanent-delete warning
4. Confirm by typing 'DELETE'
5. User record is deleted
6. All embeddings are wiped from database
7. Action is logged, but no biometric data goes into logs

### 4. List Users

```
Main Menu → [4] List users
  ↓
Display table of all users:
  - User ID
  - Username
  - Embedding count
  - Creation date
```

### 5. Authentication Activation Triggers

**Architectural Distinction: Coral is NOT contacted by app**

Coral has no direct connection to the mobile app. Three ways a Coral auth session activates:

1. **App-Authenticated Users (NO Coral Biometric Auth)**
   - User logs into app on phone (app authentication system)
   - Phone is trusted device (Cloud recognizes authenticated session)
   - Cloud backend marks unlock status as authorized in database
   - Bike unlocks immediately via cloud command
   - **Coral DOES NOT receive any message** (remains in idle mode)
   - No biometric authentication needed
   - Fastest unlock path (instant if network available)
   
2. **Physical Button Press on Coral**
   - User presses authentication button at bike
   - Coral starts biometric session (source="local")
   - User faces camera, completes face auth
   - Unlock granted if authenticated, denied otherwise

3. **PyTrack Movement Trigger (Local Direct Connection)**
   - PyTrack detects suspicious movement (accelerometer spike)
   - If no recent authentication: asks Coral to authenticate
   - Coral starts session (source="pytrack")
   - Theft prevention: user must verify with face
   - Works when phone in proximity (local connection)

4. **Cloud Auth Command (PyTrack Out of Range)**
   - PyTrack lost connection or too far away
   - OR user explicitly chooses biometric challenge via app
   - Cloud sends `start_auth` Direct Method to Coral
   - Coral starts session (source="cloud")
   - User faces camera, completes biometric auth
   - Result sent to cloud, propagated to app

**Signal Sources (Source Field Tracking):**
- `source="local"` - CLI admin testing or physical button
- `source="pytrack"` - Local movement detection
- `source="cloud"` - Remote cloud trigger (PyTrack disconnected or user choice)

**See:** `CLOUD_SIGNALING_GUIDE.md` for complete cloud signaling architecture.

## Configuration

### Enrollment Settings

Located in `config.py` under `EnrollmentControllerConfig`:

```python
@dataclass
class EnrollmentControllerConfig:
    total_timeout_sec: int = 120           # Timeout for enrollment (seconds)
    embeddings_per_user: int = 5           # Target embeddings per user
    min_frames_between_captures: int = 5   # Skip frames for angle variety
```

### Customize enrollment parameters:

```python
from config import get_config

config = get_config()
config.enrollment_controller.embeddings_per_user = 8  # More embeddings = more robust
config.enrollment_controller.total_timeout_sec = 180   # Give more time
```

## Security Considerations

**Encryption at rest**: All embeddings encrypted with AES-256-GCM  
**Local storage only**: Biometric data never leaves device  
**Secure deletion**: User removal = cascading delete of all data  
**No biometric logging**: Face data, embeddings, confidence never logged  
**Admin-only**: Enrollment requires manual admin interaction (no self-enrollment)  
**Validation**: Username format validation, user exists checks  

## Mock Mode

For testing without hardware:

```bash
# Run with mock camera + mock models
python admin_interface.py --mock

# In mock mode:
# - Camera generates synthetic frames
# - Models return mock predictions
# - No hardware required
# - Full workflow testing
```

## Integration with AuthController

The admin interface uses `AuthenticationController` for auth tests. This keeps:

- Same authentication pipeline as production
- Same liveness challenges
- Same confidence thresholds
- Consistent behavior

**Cloud Signaling Integration:**

Admin interface initializes `CloudSignalingInterface` in mock mode for development:
- No Azure credentials required for CLI testing
- Mock mode queues messages locally for verification
- When deployed with real credentials, cloud signaling automatically activates
- Signals from CLI tests: `source="local"` (orthogonal to cloud/pytrack)

## Future Enhancements

- Web-based admin dashboard instead of CLI
- Batch enrollment support
- Multi-frame embedding averaging for robustness
- Admin audit trail with secure timestamps
- Rate limiting on enrollment API
- Two-factor authentication for admin operations
- Temporary enrollment tokens/links
- Enrollment via mobile app or web portal
