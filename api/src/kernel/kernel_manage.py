# kernel_manage.py
import asyncio
import ast
import re
import queue
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, AsyncGenerator, Union, List, Optional
from jupyter_client import KernelManager as JupyterKernelManager
from fastapi.responses import StreamingResponse
from dataclasses import dataclass
from enum import Enum
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ExecutionState(Enum):
    BUSY = "busy"
    IDLE = "idle"
    ERROR = "error"
    TIMEOUT = "timeout"
    COMPLETED = "completed"

@dataclass
class ExecutionInfo:
    msg_id: str
    state: ExecutionState
    start_time: float
    last_activity: float

class ExecutionTracker:
    def __init__(self):
        self._executions: Dict[str, ExecutionInfo] = {}
        self._lock = threading.RLock()

    def add_execution(self, msg_id: str):
        with self._lock:
            self._executions[msg_id] = ExecutionInfo(
                msg_id=msg_id,
                state=ExecutionState.BUSY,
                start_time=time.time(),
                last_activity=time.time()
            )

    def get_active_count(self) -> int:
        with self._lock:
            return sum(1 for info in self._executions.values() 
                      if info.state == ExecutionState.BUSY)

    def update_execution_state(self, msg_id: str, state: ExecutionState):
        with self._lock:
            if msg_id in self._executions:
                self._executions[msg_id].state = state
                self._executions[msg_id].last_activity = time.time()

    def finalize_execution(self, msg_id: str):
        with self._lock:
            if msg_id in self._executions:
                exec_info = self._executions[msg_id]
                if exec_info.state == ExecutionState.BUSY:
                    exec_info.state = ExecutionState.COMPLETED
                exec_info.last_activity = time.time()
                logger.info(f"Execution {msg_id} completed in {time.time() - exec_info.start_time:.2f}s")

class OutputBufferManager:
    def __init__(self, max_size: int = 1024 * 1024):
        self._buffers: Dict[str, SafeOutputBuffer] = {}
        self._lock = threading.RLock()
        self.max_size = max_size

    async def process_stream_message(self, msg: dict, msg_id: str) -> AsyncGenerator[str, None]:
        output_text = self._process_message(msg, msg_id)
        if output_text:
            with self._lock:
                buffer = self._buffers.get(msg_id)
                if not buffer:
                    buffer = SafeOutputBuffer(self.max_size)
                    self._buffers[msg_id] = buffer
                
                if buffer.append(output_text):
                    yield output_text
                else:
                    yield "\n[Output buffer full. Stopping stream.]\n"

    def _process_message(self, msg: dict, msg_id: str) -> str:
        if not isinstance(msg, dict) or 'header' not in msg:
            return ""
        
        parent_header = msg.get('parent_header', {})
        if parent_header.get('msg_id') != msg_id:
            return ""
        
        msg_type = msg['header']['msg_type']
        content = msg.get('content', {})
        
        if msg_type == 'stream':
            return content.get('text', '')
        elif msg_type in ('execute_result', 'display_data'):
            return content.get('data', {}).get('text/plain', '')
        elif msg_type == 'error':
            error_text = f"\n{content.get('ename', 'Error')}: {content.get('evalue', '')}\n"
            if traceback := content.get('traceback', []):
                error_text += '\n'.join(traceback)
            return error_text
        return ""

class SafeOutputBuffer:
    def __init__(self, max_size: int):
        self.max_size = max_size
        self.buffer = []
        self.current_size = 0
        self._lock = threading.RLock()
        self.kernel_lock = threading.Lock()
        self.truncated = False
    
    def append(self, data: str) -> bool:
        with self._lock:
            data_size = len(data)
            if self.current_size + data_size > self.max_size:
                if not self.truncated:
                    self.buffer.append("\n[Output truncated due to size limit]\n")
                    self.truncated = True
                return False
            
            self.buffer.append(data)
            self.current_size += data_size
            return True

class Kernel:
    def __init__(self, kernel_name: str = 'python3'):
        self.kernel_name = kernel_name  # Store the kernel name
        self._km = JupyterKernelManager(kernel_name=self.kernel_name)
        self._client = None
        self._lock = threading.RLock()
        self._healthy = False
        self._cleanup_lock = threading.Lock()
        self._restart_in_progress = threading.Event()
        self.kernel_lock = threading.Lock()

    @property
    def client(self):
        with self._lock:
            return self._client

    @client.setter
    def client(self, new_client):
        with self._lock:
            if self._client and hasattr(self._client, 'stop_channels'):
                try:
                    self._client.stop_channels()  # Close existing channels
                except Exception as e:
                    logger.warning(f"Error stopping existing client channels: {e}")
            self._client = new_client
            if self._client:
                try:
                    self._client.start_channels()  # Start new client channels
                except Exception as e:
                    logger.error(f"Failed to start new client channels: {e}")
                    self._healthy = False
                    raise RuntimeError(f"Failed to start new client channels: {str(e)}")
    '''
        Start kernel with a timeout to avoid blocking the main thread.
    '''
    def start_kernel(self, timeout=30):
        with self._lock:
            result = {}
            # declare start_kernel in a thread to avoid blocking
            def _start():
                try:
                    self._km.start_kernel()
                    result['success'] = True
                except Exception as e:
                    result['error'] = e

            thread = threading.Thread(target=_start)
            thread.start()
            thread.join(timeout=timeout)

            if not result.get('success'):
                self._healthy = False
                try:
                    self._km.shutdown_kernel(now=True)
                except Exception:
                    pass

                if 'error' in result:
                    raise result['error']
                else:
                    raise TimeoutError(f"Kernel failed to start within {timeout}s")

            # Proceed after successful start
            self._client = self._km.client()
            self._start_channels_with_timeout(min(10, timeout))
            self._healthy = True

    '''
        Start channels with a timeout to ensure they are ready.
    '''
    def _start_channels_with_timeout(self, timeout: int):
        start_time = time.time()
        last_exception = None
        
        while time.time() - start_time < timeout:
            try:
                self._client.start_channels()
                if all(channel.is_alive() for channel in [
                    self._client.shell_channel,
                    self._client.iopub_channel
                ] if channel is not None):
                    return
            except Exception as e:
                last_exception = e
            time.sleep(0.5)
        
        if last_exception:
            raise RuntimeError(f"Failed to start channels: {last_exception}")
        raise RuntimeError("Failed to start channels within timeout")

    def is_kernel_alive(self) -> bool:
        with self._lock:
            try:
                return self._healthy and self._km.is_alive() and all(
                    channel.is_alive() for channel in [
                        self._client.shell_channel,
                        self._client.iopub_channel
                    ] if channel is not None
                )
            except Exception:
                return False

    def ensure_channels_active(self):
        with self._lock:
            if not self.is_kernel_alive():
                self._start_channels_with_timeout(5)

    def interrupt_kernel(self):
        """Safely interrupt kernel execution"""
        with self._lock:
            try:
                if hasattr(self._client, 'interrupt'):
                    self._client.interrupt()
                else:
                    # Fallback method for kernels without direct interrupt
                    if hasattr(self._km, 'interrupt_kernel'):
                        self._km.interrupt_kernel()
                    else:
                        self.restart_kernel()  # Full restart if no interrupt available
            except Exception as e:
                logger.error(f"Interrupt failed: {e}")
                raise RuntimeError("Failed to interrupt kernel")

    def restart_kernel(self) -> Dict[str, Any]:
        """Synchronously restart the kernel with proper cleanup and re-initialization."""
        if self._restart_in_progress.is_set():
            return {"status": "error", "message": "Restart already in progress"}

        self._restart_in_progress.set()
        try:
            logger.info("Starting kernel restart procedure")

            # --- Shutdown the OLD kernel and client ---
            if self._client and hasattr(self._client, 'stop_channels'):
                try:
                    self._client.stop_channels()
                except Exception as e:
                    logger.warning(f"Error stopping channels: {e}")

            if self._km and self._km.is_alive():
                try:
                    self._km.shutdown_kernel(now=True)
                except Exception as e:
                    logger.warning(f"Graceful shutdown of old kernel failed: {e}")
            
            # --- THE FIX: Create a NEW KernelManager instance ---
            logger.info("Creating a new kernel manager instance.")
            self._km = JupyterKernelManager(kernel_name=self.kernel_name)
            
            # --- Start the NEW kernel and client ---
            try:
                self._km.start_kernel()
                self._client = self._km.client()
                self._start_channels_with_timeout(10) # Use your existing helper
                self._healthy = True
                logger.info("Kernel restarted and new client is active.")
            except Exception as e:
                logger.error(f"Failed to start new kernel after restart: {e}")
                self._healthy = False
                raise RuntimeError(f"Failed to start new kernel: {str(e)}")

            return {"status": "success", "message": "Kernel restarted successfully"}

        except Exception as e:
            logger.error(f"Kernel restart procedure failed: {e}")
            self._healthy = False
            return {"status": "error", "message": f"Restart failed: {str(e)}"}
        finally:
            self._restart_in_progress.clear()



    def _trigger_kernel_restart(self):
        """Trigger kernel restart in a separate thread to avoid blocking."""
        if self._restart_in_progress.is_set():
            logger.info("Kernel restart already in progress")
            return
        
        restart_thread = threading.Thread(
            target=self._restart_kernel_async,
            daemon=True,
            name="KernelRestart"
        )
        restart_thread.start()

    def shutdown_kernel(self, now: bool = False):
        """Shutdown the kernel with proper async/sync handling"""
        try:
            if now:
                # Forceful shutdown path
                try:
                    if hasattr(self._km, 'kill_kernel'):
                        self._km.kill_kernel()
                    elif hasattr(self._km, 'shutdown_kernel'):
                        # Try sync shutdown first
                        self._km.shutdown_kernel(now=True)
                    else:
                        logger.warning("No force shutdown method available")
                        raise RuntimeError("No force shutdown available")
                except Exception as e:
                    logger.warning(f"Force shutdown attempt failed: {e}")
                    # Last resort - terminate process directly
                    if hasattr(self._km, 'proc') and self._km.proc:
                        self._km.proc.terminate()
            else:
                # Graceful shutdown path
                try:
                    if hasattr(self._km, 'shutdown_kernel'):
                        # Try sync shutdown first
                        self._km.shutdown_kernel()
                    else:
                        logger.warning("No graceful shutdown method available")
                        raise RuntimeError("No graceful shutdown available")
                except Exception as e:
                    logger.warning(f"Graceful shutdown failed: {e}")
                    # Fall back to force if graceful fails
                    return self.shutdown_kernel(now=True)

            # Ensure cleanup regardless of shutdown method
            self._post_shutdown_cleanup()

        except Exception as e:
            logger.error(f"Shutdown error: {e}")
            raise RuntimeError(f"Kernel shutdown failed: {e}")

    def _post_shutdown_cleanup(self):
        """Synchronous cleanup after shutdown"""
        try:
            # 1. Stop channels if they exist
            if hasattr(self, '_client') and self._client:
                try:
                    if hasattr(self._client, 'stop_channels'):
                        self._client.stop_channels()
                except Exception as e:
                    logger.warning(f"Error stopping channels: {e}")

            # 2. Flush any remaining messages
            try:
                self.flush_channels(timeout=2)  # Short timeout for flush
            except Exception as e:
                logger.warning(f"Channel flush failed: {e}")

            # 3. Clear internal state
            self._healthy = False
            logger.info("Post-shutdown cleanup completed")
        except Exception as e:
            logger.error(f"Post-shutdown cleanup failed: {e}")
    
    def _restart_kernel_async(self):
        """Async kernel restart with proper synchronization."""
        try:
            self._restart_in_progress.set()
            logger.info("Starting kernel restart...")
            with self.kernel_lock:
                try:
                    self._shutdown_kernel_internal()
                    self._km.restart_kernel()
                    self.client = self._km.client()
                    self._start_channels_with_timeout(10)
                    for _ in range(10):
                        if self.client.is_alive():
                            try:
                                self.client.kernel_info()
                                self._kernel_healthy = True
                                logger.info("Kernel restart successful")
                                return
                            except Exception:
                                pass
                        time.sleep(1)
                    logger.error("Kernel restart failed - not responding")
                    self._kernel_healthy = False
                except Exception as e:
                    logger.error(f"Kernel restart failed: {e}")
                    self._kernel_healthy = False
        finally:
            self._restart_in_progress.clear()
    
    def _shutdown_kernel_internal(self):
        """Internal method to shutdown the kernel without recursive calls."""
        logger.info("Shutting down kernel internally...")
        try:
            with self.kernel_lock:
                if hasattr(self.client, 'stop_channels'):
                    self.client.stop_channels()
                if self._km:
                    self.shutdown_kernel()
            logger.info("Kernel shutdown completed internally")
        except Exception as e:
            logger.error(f"Error during internal kernel shutdown: {e}")

    def cleanup(self, force=False):
        """Thread-safe kernel cleanup with force option"""
        with self._cleanup_lock:
            try:
                # Step 1: Flush channels with timeout
                try:
                    self.flush_channels(timeout=5)  # Added timeout parameter
                except Exception as e:
                    logger.warning(f"Channel flush warning: {str(e)}")

                # Step 2: Stop channels if they exist
                if hasattr(self._client, 'stop_channels'):
                    try:
                        self._client.stop_channels()
                    except Exception as e:
                        logger.warning(f"Channel stop warning: {str(e)}")

                # Step 3: Forceful cleanup if requested
                if force:
                    if hasattr(self, '_km'):
                        try:
                            self._km.shutdown_kernel(now=True)
                        except Exception as e:
                            logger.error(f"Force shutdown error: {str(e)}")

                # Step 4: Cleanup executor if exists
                if hasattr(self, '_executor'):
                    self._executor.shutdown(wait=False)

                logger.info("Cleanup completed successfully")
                return True
                
            except Exception as e:
                logger.error(f"Cleanup failed: {str(e)}")
                return False
            finally:
                self._is_cleaned_up = True

    def flush_channels(self, timeout=0.5):
        """Interrupt-safe channel flushing"""
        if not hasattr(self, '_client') or not self._client:
            return True

        start_time = time.time()
        
        try:
            with self._cleanup_lock:
                for channel_name in ['shell', 'iopub', 'stdin', 'hb']:
                    # Safe attribute check
                    channel = getattr(self._client, f"{channel_name}_channel", None)
                    if not channel:
                        continue

                    # Critical section - prevent interrupts
                    with contextlib.ExitStack() as stack:
                        stack.enter_context(
                            threading._ignore_during_cleanup()  # Disable GC
                        )
                        
                        try:
                            # Non-blocking check
                            channel.get_msg(timeout=min(0.05, timeout))
                            logger.debug(f"Flushed {channel_name}")
                        except (queue.Empty, KeyboardInterrupt):
                            continue
                        except Exception as e:
                            logger.debug(f"Flush error on {channel_name}: {e}")
                            
                    # Check total timeout
                    if time.time() - start_time >= timeout:
                        break

            return True

        except Exception:
            return False

    def get_kernel_pid(self) -> Optional[int]:
        """Get the kernel process ID if available"""
        with self._lock:
            try:
                return self._km.provisioner.get_pid() if hasattr(self._km, 'provisioner') else None
            except Exception:
                return None

    def ensure_channels_active(self):
        """Ensure all channels are active and restart if needed"""
        with self._lock:
            if not self.is_kernel_alive():
                raise RuntimeError("Kernel is not alive")
                
            try:
                # Refresh channels if needed
                if not all(channel.is_alive() for channel in [
                    self._client.shell_channel,
                    self._client.iopub_channel
                                                   
                ] if channel is not None):
                    # restarts channels if not alive
                    self._client.stop_channels()
                    self._client.start_channels()
                    
                    # Verify channels are up
                    for _ in range(3):
                        if all(channel.is_alive() for channel in [
                            self._client.shell_channel,
                            self._client.iopub_channel
                        ]):
                            return
                        time.sleep(0.5)
                    
                    raise RuntimeError("Failed to activate channels")
            except Exception as e:
                logger.error(f"Channel activation failed: {e}")
                raise

    def reconnect_client(self):
        """Reconnect kernel client, useful after resume from pause or cleanup."""
        try:
            self.client = self._km.client()
            logger.info("Kernel client reconnected successfully")
        except Exception as e:
            logger.error(f"Failed to reconnect kernel client: {e}")




