"""
Admin interface - temporary CLI for user enrollment, authentication testing, and removal.
Manages new user enrollment, face biometrics capture, auth testing, and user deletion.
"""

import sys
import time
import os
from typing import Optional
from datetime import datetime

# Add Coral directory to path if running from parent
coral_dir = os.path.join(os.path.dirname(__file__))
if coral_dir not in sys.path:
    sys.path.insert(0, coral_dir)

# Check for required dependencies
try:
    from config import get_config
    from logger import logger_admin
    from errors import EnrollmentError, AuthenticationError
    from camera_interface import CameraInterface, get_camera
    from embedding_store import EmbeddingStore
    from user_manager import UserManager
    from enrollment_controller import EnrollmentController, EnrollmentResult
    from auth_controller import AuthenticationController, AuthenticationResult
    from cloud_signaling_interface import CloudInterface
    from camera_display import CameraDisplay
except ImportError as e:
    print("\n" + "="*70)
    print("ERROR: Missing dependencies")
    print("="*70)
    print(f"\nFailed to import: {e}")
    print("\nTo install required packages, run:")
    print("  pip install -r requirements.txt")
    print("="*70 + "\n")
    sys.exit(1)


class AdminInterface:
    """
    Temporary admin CLI for:
    - New user enrollment (capture face + embeddings)
    - Authentication testing (recognize user from camera)
    - User removal from database
    """

    def __init__(self, mock_mode: bool = False):
        """
        Initialize admin interface.

        Args:
            mock_mode: Use mock camera/models for testing
        """
        self.config = get_config()
        self.mock_mode = mock_mode

        # Initialize components
        self.embedding_store = EmbeddingStore()
        self.user_manager = UserManager(self.embedding_store)
        self.enrollment_controller = EnrollmentController(
            embedding_store=self.embedding_store,
            mock_mode=mock_mode
        )

        self.auth_controller = AuthenticationController(
            embedding_store=self.embedding_store,
            mock_mode=mock_mode
        )
        self.cloud_interface = CloudInterface(mock_mode=mock_mode)
        self.camera = get_camera(mock=mock_mode)

        logger_admin.info("Admin interface initialized")

    # ========== Main Menu ==========

    def show_main_menu(self):
        """Display main menu and handle user input."""
        while True:
            self._clear_screen()
            self._print_banner("ADMIN INTERFACE - Coral Face Recognition System")

            print("\n=== MAIN MENU ===")
            print("1. Enroll new user (capture face embeddings)")
            print("2. Test authentication (recognize user from camera)")
            print("3. Remove user from database")
            print("4. List all enrolled users")
            print("5. Debug: Test face recognition (experimental)")
            print("6. Exit")

            choice = input("\nSelect option (1-6): ").strip()

            if choice == "1":
                self._enrollment_menu()
            elif choice == "2":
                self._authentication_menu()
            elif choice == "3":
                self._removal_menu()
            elif choice == "4":
                self._list_users()
            elif choice == "5":
                self._debug_face_recognition()
            elif choice == "6":
                self._print_info("Exiting admin interface. Goodbye!")
                break
            else:
                self._print_error("Invalid option. Please try again.")
                input("Press Enter to continue...")

    # ========== Enrollment Flow ==========

    def _enrollment_menu(self):
        """Enrollment submenu - get user info and start capture."""
        self._clear_screen()
        self._print_banner("ENROLL NEW USER")

        print("\n=== User Information ===")

        # Get user info
        user_id = self._get_input("Enter user ID (e.g., user_001): ", required=True)
        if not user_id:
            return

        # Check if user exists
        if self.embedding_store.get_user_info(user_id):
            self._print_error(f"User {user_id} already exists!")
            input("Press Enter to continue...")
            return

        username = self._get_input("Enter user name (e.g., John Doe): ", required=True)
        is_valid_username = self.user_manager._is_valid_username(username)
        while not is_valid_username:
            self._print_error(
                f"Invalid username. Must be {self.user_manager.config.min_username_length}-"
                f"{self.user_manager.config.max_username_length} characters."
            )
            username = self._get_input("Enter user name (e.g., John Doe): ", required=True)
            is_valid_username = self.user_manager._is_valid_username(username)

        if not username:
            return

        # Optional metadata
        print("\nOptional metadata (leave blank to skip):")
        role = self._get_input("Role (owner/guest/staff): ", required=False)
        email = self._get_input("Email: ", required=False)

        metadata = {}
        if role:
            metadata["role"] = role
        if email:
            metadata["email"] = email

        # Confirm
        print(f"\n--- Summary ---")
        print(f"User ID: {user_id}")
        print(f"Username: {username}")
        if metadata:
            print(f"Metadata: {metadata}")
        print(f"Target embeddings: {self.enrollment_controller.config.embeddings_per_user}")

        confirm = self._get_input("\nProceed with enrollment? (y/n): ", required=True).lower()
        if confirm != "y":
            self._print_info("Enrollment cancelled.")
            input("Press Enter to continue...")
            return

        # Start enrollment
        self._print_info("Initializing camera...")
        try:
            self.camera.open()
            self.camera.start_capture()
        except Exception as e:
            self._print_error(f"Failed to initialize camera: {e}")
            input("Press Enter to continue...")
            return

        try:
            session_id = self.enrollment_controller.start_enrollment(
                user_id=user_id,
                username=username,
                metadata=metadata or None
            )
            self._print_success(f"Enrollment started (session: {session_id})")
            self._enrollment_capture_loop()
        except EnrollmentError as e:
            self._print_error(f"Enrollment failed: {e}")
        finally:
            self.camera.stop_capture()
            self.camera.close()
            input("Press Enter to continue...")

    def _enrollment_capture_loop(self):
        """Loop to capture frames during enrollment."""
        print("\n=== Capturing Face Embeddings ===")
        print("Keep your face in front of the camera.")
        print("Move slightly to capture different angles.")
        print("Press Ctrl+C to stop early, or close display window.\n")

        display = CameraDisplay() if not self.mock_mode else None

        try:
            while True:
                # Get frame
                try:
                    frame, _ = self.camera.get_frame_no_wait() or self.camera.get_frame(timeout_sec=0.1)
                except:
                    continue

                # Process frame
                status = self.enrollment_controller.capture_enrollment_frame((frame, 0))

                # Display frame with overlay
                if display and frame is not None:
                    try:
                        from face_detector import get_face_detector
                        detector = get_face_detector(mock=self.mock_mode)
                        faces = detector.detect(frame)

                        progress = status.get("progress", "0/0")
                        display.show_frame(
                            frame,
                            title="ENROLLMENT: Capture Face",
                            faces=faces,
                            text_lines=[
                                f"Embeddings: {progress}",
                                "Keep steady, move slowly for different angles"
                            ],
                            progress_text=f"Faces detected: {len(faces)}"
                        )
                        display.wait_key(1)

                        if display.closed:
                            break
                    except:
                        pass

                # Console feedback
                self._print_enrollment_status(status)

                if status["state"] == "success":
                    self._print_success(
                        f"\nEnrollment complete! Captured {status['embeddings_captured']} embeddings."
                    )
                    break

                if status["state"] == "failure":
                    self._print_error(f"\nEnrollment failed: {status['error_code']}")
                    break

                time.sleep(1.0 / get_config().camera.fps)

        except KeyboardInterrupt:
            self._print_warning("\nEnrollment interrupted by user.")
            end_result = self.enrollment_controller.end_enrollment()
            if end_result == EnrollmentResult.IN_PROGRESS:
                self._print_info("Cancelling enrollment...")
                self.enrollment_controller.cancel_enrollment()
        finally:
            if display:
                display.close()

    def _print_enrollment_status(self, status: dict):
        """Print enrollment status on single line (real-time update)."""
        progress = status.get("progress", "0/0")
        error = status.get("error_code", "")
        frames = status.get("frames_processed", 0)

        status_str = f"[Frames: {frames}] [Embeddings: {progress}]"
        if error:
            status_str += f" [Error: {error}]"

        print(f"\r{status_str:<80}", end="", flush=True)

    # ========== Authentication Testing ==========

    def _authentication_menu(self):
        """Authentication menu - test face recognition."""
        self._clear_screen()
        self._print_banner("TEST AUTHENTICATION")

        users = self.user_manager.get_all_users()
        if not users:
            self._print_error("No enrolled users. Please enroll a user first.")
            input("Press Enter to continue...")
            return

        print(f"\nEnrolled users: {len(users)}")
        for user in users:
            embedding_count = self.embedding_store.get_embedding_count(user["user_id"])
            print(f"  - {user['username']} ({user['user_id']}) - {embedding_count} embeddings")

        confirm = self._get_input("\nStart authentication test? (y/n): ", required=True).lower()
        if confirm != "y":
            self._print_info("Authentication test cancelled.")
            input("Press Enter to continue...")
            return

        # Initialize camera
        self._print_info("Initializing camera...")
        try:
            self.camera.open()
            self.camera.start_capture()
        except Exception as e:
            self._print_error(f"Failed to initialize camera: {e}")
            input("Press Enter to continue...")
            return

        try:
            session_id = self.auth_controller.start_authentication()
            self._print_success(f"Authentication started (session: {session_id})")
            print("\nShow your face to the camera for recognition.")
            print("Complete the liveness challenge when prompted.")
            print("Press Ctrl+C to stop.\n")

            self._authentication_loop()

        except AuthenticationError as e:
            self._print_error(f"Authentication failed: {e}")
        finally:
            self.camera.stop_capture()
            self.camera.close()
            input("Press Enter to continue...")

    def _authentication_loop(self):
        """Loop to process frames during authentication."""
        display = CameraDisplay() if not self.mock_mode else None

        # Cache detector to avoid reloading each frame
        from face_detector import get_face_detector
        face_detector = get_face_detector(mock=self.mock_mode)

        try:
            while True:
                # Get frame
                try:
                    frame, _ = self.camera.get_frame_no_wait() or self.camera.get_frame(timeout_sec=0.1)
                except:
                    continue

                # Process frame
                status = self.auth_controller.process_frame((frame, 0))

                # Display frame with instructions
                if display and frame is not None:
                    try:
                        faces = face_detector.detect(frame)

                        user_id = status.get("user_id", "?")
                        confidence = status.get("confidence", 0.0)
                        challenge = status.get("current_challenge", "")
                        error = status.get("error_code", "")
                        frames = status.get("frames_processed", 0)

                        text_lines = []
                        if user_id == "?":
                            text_lines.append("Detecting face... Keep steady")
                        elif not status.get("liveness_on_going"):
                            text_lines.append(f"Recognized: {user_id}")
                            text_lines.append(f"Confidence: {confidence:.1%}")
                        else:
                            text_lines.append(f"Liveness challenge:")
                            text_lines.append(challenge)

                        # Debug info
                        debug_lines = [
                            f"Faces: {len(faces)} detected",
                            f"Error: {error}" if error else "No error",
                            f"Frame: {frames}",
                        ]
                        debug_text = "\n".join(debug_lines)

                        display.show_frame(
                            frame,
                            title="AUTHENTICATION: Verify Face",
                            faces=faces,
                            text_lines=text_lines,
                            progress_text=f"User: {user_id} | Conf: {confidence:.1%}",
                            debug_text=debug_text
                        )
                        display.wait_key(1)

                        if display.closed:
                            break
                    except:
                        pass

                # Console feedback
                self._print_authentication_status(status)

                if status["state"] == "success":
                    self._print_success(
                        f"\n[OK] AUTHENTICATION SUCCESS!"
                    )
                    print(f"  User: {status.get('user_id')}")
                    print(f"  Confidence: {status.get('confidence', 0.0):.2%}")
                    print(f"  Liveness: Verified")
                    break

                if status["state"] == "failure":
                    self._print_error(f"\n[ERROR] AUTHENTICATION FAILED")
                    error = status.get("error_code", "Unknown error")
                    print(f"  Error: {error}")
                    break

                if status["state"] == "timeout":
                    self._print_error(f"\n[ERROR] AUTHENTICATION TIMEOUT")
                    break

                # Continue processing frames for "in_progress" state
                time.sleep(1.0 / get_config().camera.fps)

        except KeyboardInterrupt:
            self._print_warning("\nAuthentication interrupted by user.")
            self.auth_controller.end_session()
        finally:
            if display:
                display.close()

    def _print_authentication_status(self, status: dict):
        """Print authentication status on single line (real-time update)."""
        user_id = status.get("user_id", "?")
        confidence = status.get("confidence", 0.0)
        liveness_ongoing = status.get("liveness_on_going", False)
        challenge = status.get("current_challenge", "")
        error = status.get("error_code", "")

        status_str = f"[User: {user_id}] [Confidence: {confidence:.1%}]"

        if liveness_ongoing:
            status_str += f" [Challenge: {challenge}]"
        elif user_id != "?":
            status_str += " [Face recognized - awaiting liveness]"

        if error:
            status_str += f" [Error: {error}]"

        print(f"\r{status_str:<100}", end="", flush=True)

    # ========== User Removal ==========

    def _removal_menu(self):
        """User removal menu - delete user and embeddings."""
        self._clear_screen()
        self._print_banner("REMOVE USER")

        users = self.user_manager.get_all_users()
        if not users:
            self._print_error("No enrolled users.")
            input("Press Enter to continue...")
            return

        print(f"\nEnrolled users:")
        for i, user in enumerate(users, 1):
            embedding_count = self.embedding_store.get_embedding_count(user["user_id"])
            print(f"  {i}. {user['username']} ({user['user_id']}) - {embedding_count} embeddings")

        user_id = self._get_input("\nEnter user ID to remove (or 'cancel'): ", required=True)
        if user_id.lower() == "cancel":
            self._print_info("Removal cancelled.")
            input("Press Enter to continue...")
            return

        # Verify user exists
        user_info = self.embedding_store.get_user_info(user_id)
        if not user_info:
            self._print_error(f"User not found: {user_id}")
            input("Press Enter to continue...")
            return

        # Double confirm
        print(f"\n[WARNING] WARNING: This will permanently delete:")
        print(f"  User: {user_info['username']} ({user_id})")
        print(f"  All face embeddings and metadata")
        print("\nThis action CANNOT be undone.")

        confirm = self._get_input("\nType 'DELETE' to confirm (or press Enter to cancel): ", required=True)
        if confirm != "DELETE":
            self._print_info("Removal cancelled.")
            input("Press Enter to continue...")
            return

        # Remove user
        try:
            self.user_manager.remove_user(user_id)
            self._print_success(f"[OK] User removed: {user_info['username']} ({user_id})")
            self._print_info("All face embeddings have been securely deleted.")
        except Exception as e:
            self._print_error(f"Failed to remove user: {e}")

        input("Press Enter to continue...")

    # ========== Debug ==========

    def _debug_face_recognition(self):
        """Debug: Test face recognition and embedding matching."""
        self._clear_screen()
        self._print_banner("DEBUG: Face Recognition Test")

        users = self.user_manager.get_all_users()
        if not users:
            self._print_error("No enrolled users. Enroll a user first.")
            input("Press Enter to continue...")
            return

        print(f"\nEnrolled users: {len(users)}")
        for i, user in enumerate(users, 1):
            emb_count = self.embedding_store.get_embedding_count(user["user_id"])
            print(f"  {i}. {user['username']} ({user['user_id']}) - {emb_count} embeddings")

        user_id = self._get_input("\nSelect user ID to test: ", required=True)
        user_info = self.embedding_store.get_user_info(user_id)
        if not user_info:
            self._print_error(f"User not found: {user_id}")
            input("Press Enter to continue...")
            return

        print(f"\n=== Testing {user_info['username']} ({user_id}) ===")

        # Get stored embeddings
        stored_embeddings = self.embedding_store.get_user_embeddings(user_id)
        print(f"[OK] Loaded {len(stored_embeddings)} embeddings from database")

        # Show embedding info
        print(f"\nEmbedding details:")
        for i, emb in enumerate(stored_embeddings[:3], 1):  # Show first 3
            print(f"  {i}. Dimension: {emb.dimension}, Vector sample: {emb.vector[:5]}...")

        # Capture test frame
        print(f"\n[INFO] Initializing camera for test...")
        try:
            self.camera.open()
            self.camera.start_capture()
        except Exception as e:
            self._print_error(f"Camera init failed: {e}")
            input("Press Enter to continue...")
            return

        try:
            print(f"Show face to camera. Capturing in 2 seconds...")
            time.sleep(2)

            # Capture frame
            frame, _ = self.camera.get_frame(timeout_sec=1.0)
            print(f"[OK] Frame captured: {frame.shape}")

            # Detect face
            faces = self.enrollment_controller.face_detector.detect(frame)
            print(f"[OK] Faces detected: {len(faces)}")

            if not faces:
                self._print_error("No face detected in frame!")
                input("Press Enter to continue...")
                return

            # Generate embedding
            face = faces[0]
            face_crop = face.crop_from_frame(frame)
            
            test_embedding = self.enrollment_controller.face_recognizer.generate_embedding(face_crop)
            print(f"Backend: {self.enrollment_controller.face_recognizer.backend.model_name}")
            print(f"Embedding shape: {test_embedding.vector.shape}, range: {test_embedding.vector.min():.3f}-{test_embedding.vector.max():.3f}")
            
            print(f"[OK] Embedding generated: {test_embedding.dimension}D")
            print(f"     Vector sample: {test_embedding.vector[:5]}...")

            # Compare with stored
            print(f"\n=== Matching test embedding against {len(stored_embeddings)} stored embeddings ===")
            distances = []
            for i, stored_emb in enumerate(stored_embeddings):
                dist = test_embedding.distance_to(stored_emb)
                distances.append(dist)
                confidence = 1.0 - (dist / 2.0)
                match = "[MATCH]" if confidence > 0.6 else "[NOMATCH]"
                print(f"  {i+1}. Distance: {dist:.4f}, Confidence: {confidence:.1%} {match}")

            # Summary
            best_dist = min(distances)
            best_idx = distances.index(best_dist)
            best_conf = 1.0 - (best_dist / 2.0)
            threshold = 0.6  # From config

            print(f"\n=== RESULT ===")
            print(f"Best match: Embedding #{best_idx+1}")
            print(f"Distance: {best_dist:.4f}")
            print(f"Confidence: {best_conf:.1%}")
            print(f"Threshold: {1.0 - (threshold / 2.0):.1%}")

            if best_conf > threshold:
                self._print_success(f"✓ Recognition WOULD PASS")
            else:
                self._print_error(f"✗ Recognition WOULD FAIL - confidence too low")

        except Exception as e:
            self._print_error(f"Test failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.camera.stop_capture()
            self.camera.close()
            input("Press Enter to continue...")


    def _list_users(self):
        """List all enrolled users."""
        self._clear_screen()
        self._print_banner("ENROLLED USERS")

        users = self.user_manager.get_all_users()

        if not users:
            self._print_info("No enrolled users.")
            input("Press Enter to continue...")
            return

        print(f"\nTotal users: {len(users)}\n")
        print(f"{'ID':<15} {'Username':<20} {'Embeddings':<15} {'Created':<20}")
        print("-" * 70)

        for user in users:
            user_id = user["user_id"]
            username = user["username"]
            embedding_count = self.embedding_store.get_embedding_count(user_id)
            created_at = datetime.fromtimestamp(user["created_at"]).strftime("%Y-%m-%d %H:%M:%S")

            print(f"{user_id:<15} {username:<20} {embedding_count:<15} {created_at:<20}")

        input("\nPress Enter to continue...")

    # ========== Utility Methods ==========

    def _clear_screen(self):
        """Clear terminal screen."""
        import os
        os.system("cls" if os.name == "nt" else "clear")

    def _print_banner(self, title: str):
        """Print banner header."""
        width = 70
        print("=" * width)
        print(f" {title.center(width - 2)}")
        print("=" * width)

    def _print_success(self, message: str):
        """Print success message."""
        print(f"[OK] {message}")

    def _print_error(self, message: str):
        """Print error message."""
        print(f"[ERROR] {message}")

    def _print_warning(self, message: str):
        """Print warning message."""
        print(f"[WARNING] {message}")

    def _print_info(self, message: str):
        """Print info message."""
        print(f"[INFO] {message}")

    def _get_input(self, prompt: str, required: bool = False) -> str:
        """
        Get user input with optional validation.

        Args:
            prompt: Input prompt
            required: If True, don't accept empty input

        Returns:
            User input or empty string
        """
        while True:
            user_input = input(prompt).strip()
            if required and not user_input:
                self._print_error("Input required. Please try again.")
                continue
            return user_input


def main():
    """Main entry point for admin interface."""
    import argparse

    parser = argparse.ArgumentParser(description="Admin interface for Coral face recognition")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock camera and models (for testing without hardware)"
    )

    args = parser.parse_args()

    try:
        admin = AdminInterface(mock_mode=args.mock)
        if args.mock:
            print("\n[WARNING] Running in MOCK MODE - no hardware required\n")
        admin.show_main_menu()
    except KeyboardInterrupt:
        print("\n\nAdmin interface interrupted.")
    except Exception as e:
        print(f"\nFatal error: {e}")
        logger_admin.error(f"Fatal error in admin interface: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
