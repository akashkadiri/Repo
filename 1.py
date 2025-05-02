#!/usr/bin/env python3
"""
Git Repository Branch Comparison Tool

This script downloads a Git repository (including both master/main and feature branches) 
to a temporary folder locally, then compares the files to identify differences.

Usage:
    python git_compare.py --repo URL --master MASTER_BRANCH --feature FEATURE_BRANCH [--auth USER:TOKEN]

Example:
    python git_compare.py --repo https://github.com/user/repo.git --master main --feature dev
    python git_compare.py --repo https://github.com/user/repo.git --feature dev --auth username:personal_token
"""

import os
import sys
import argparse
import tempfile
import shutil
import logging
import json
import time
import signal
from subprocess import Popen, PIPE, TimeoutExpired
from typing import List, Dict, Any, Optional, Union
from urllib.parse import urlparse

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_TIMEOUT = 300  # 5 minutes timeout for Git operations
DEFAULT_MAIN_BRANCHES = ["master", "main"]


class GitTimeoutError(Exception):
    """Raised when a Git operation times out."""
    pass


class GitError(Exception):
    """Raised when a Git operation fails."""
    pass


class GitCompare:
    """Class to handle Git repository comparison operations."""
    
    def __init__(
        self, 
        repo_url: str, 
        master_branch: Optional[str] = None, 
        feature_branch: str = None,
        auth: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT
    ):
        """
        Initialize the GitCompare object with repository and branch information.
        
        Args:
            repo_url: URL of the Git repository to clone
            master_branch: Name of the master/main branch (if None, will try common defaults)
            feature_branch: Name of the feature branch to compare against master
            auth: Authentication in format "username:token" for private repositories
            timeout: Timeout in seconds for Git operations
        """
        self.repo_url = repo_url
        self.master_branch = master_branch
        self.feature_branch = feature_branch
        self.auth = auth
        self.timeout = timeout
        self.temp_dir = None
        self.repo_name = self._get_repo_name(repo_url)
        
        # Validate inputs
        self._validate_inputs()
    
    def _validate_inputs(self):
        """Validate the input parameters."""
        # Check if repository URL is provided and valid
        if not self.repo_url:
            raise ValueError("Repository URL must be provided")
        
        # Basic URL validation
        parsed_url = urlparse(self.repo_url)
        if not all([parsed_url.scheme, parsed_url.netloc]):
            raise ValueError(f"Invalid repository URL: {self.repo_url}")
        
        # Check if feature branch is provided
        if not self.feature_branch:
            raise ValueError("Feature branch name must be provided")
    
    def _get_repo_name(self, repo_url: str) -> str:
        """
        Extract repository name from URL.
        
        Args:
            repo_url: URL of the Git repository
            
        Returns:
            Repository name
        """
        parsed_url = urlparse(repo_url)
        path = parsed_url.path.strip('/')
        
        # Handle different URL formats
        if path.endswith('.git'):
            path = path[:-4]
        
        return os.path.basename(path)
    
    def _run_git_command(
        self, 
        command: List[str], 
        cwd: str = None, 
        timeout: int = None,
        auth_env: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """
        Run a Git command and return the result.
        
        Args:
            command: List of command arguments
            cwd: Working directory for the command
            timeout: Command timeout in seconds
            auth_env: Environment variables for authentication
            
        Returns:
            Dictionary with stdout, stderr, and return code
            
        Raises:
            GitTimeoutError: If the command times out
            GitError: If the command fails
        """
        if timeout is None:
            timeout = self.timeout
            
        env = os.environ.copy()
        if auth_env:
            env.update(auth_env)
            
        start_time = time.time()
        logger.debug(f"Running command: {' '.join(command)}")
        
        try:
            process = Popen(command, stdout=PIPE, stderr=PIPE, cwd=cwd, env=env, text=True)
            stdout, stderr = process.communicate(timeout=timeout)
            
            elapsed = time.time() - start_time
            logger.debug(f"Command completed in {elapsed:.2f}s with return code {process.returncode}")
            
            return {
                "stdout": stdout,
                "stderr": stderr,
                "returncode": process.returncode,
                "elapsed": elapsed
            }
            
        except TimeoutExpired:
            # Kill the process if it times out
            process.kill()
            stdout, stderr = process.communicate()
            raise GitTimeoutError(f"Command timed out after {timeout} seconds: {' '.join(command)}")
    
    def _setup_auth_for_url(self) -> Dict[str, str]:
        """
        Set up authentication for the repository URL.
        
        Returns:
            Environment variables with authentication
        """
        if not self.auth:
            return {}
            
        try:
            username, token = self.auth.split(':', 1)
            
            # Create URL with authentication
            parsed_url = urlparse(self.repo_url)
            netloc = f"{username}:{token}@{parsed_url.netloc}"
            authenticated_url = parsed_url._replace(netloc=netloc).geturl()
            
            # Set up Git credential helper environment
            return {
                "GIT_ASKPASS": "echo",
                "GIT_USERNAME": username,
                "GIT_PASSWORD": token
            }
            
        except ValueError:
            logger.warning("Invalid authentication format. Expected 'username:token'")
            return {}
    
    def clone_repository(self) -> str:
        """
        Clone the repository to a temporary directory.
        
        Returns:
            Path to the cloned repository
            
        Raises:
            GitError: If cloning fails
        """
        self.temp_dir = tempfile.mkdtemp(prefix=f"git-compare-{self.repo_name}-")
        logger.info(f"Created temporary directory: {self.temp_dir}")
        
        repo_path = os.path.join(self.temp_dir, self.repo_name)
        auth_env = self._setup_auth_for_url()
        
        try:
            # Attempt to clone the repository
            result = self._run_git_command(
                ["git", "clone", "--no-checkout", self.repo_url, repo_path],
                auth_env=auth_env
            )
            
            if result["returncode"] != 0:
                error_msg = result["stderr"].strip()
                
                # Provide helpful error messages for common issues
                if "Authentication failed" in error_msg or "could not read Username" in error_msg:
                    raise GitError(
                        f"Authentication failed for {self.repo_url}. "
                        "Use --auth username:token for private repositories."
                    )
                elif "repository not found" in error_msg:
                    raise GitError(
                        f"Repository not found: {self.repo_url}. "
                        "Check if the URL is correct and you have access to it."
                    )
                else:
                    raise GitError(f"Failed to clone repository: {error_msg}")
            
            logger.info(f"Successfully cloned repository to {repo_path}")
            return repo_path
            
        except Exception as e:
            # Clean up on error
            self.cleanup()
            raise e
    
    def fetch_branches(self, repo_path: str) -> List[str]:
        """
        Fetch all branches from the remote repository.
        
        Args:
            repo_path: Path to the repository
            
        Returns:
            List of available branches
            
        Raises:
            GitError: If fetching fails
        """
        auth_env = self._setup_auth_for_url()
        
        try:
            # Fetch all branches
            result = self._run_git_command(
                ["git", "fetch", "--all"], 
                cwd=repo_path,
                auth_env=auth_env
            )
            
            if result["returncode"] != 0:
                raise GitError(f"Failed to fetch branches: {result['stderr']}")
            
            # List all remote branches
            result = self._run_git_command(
                ["git", "branch", "-r"], 
                cwd=repo_path
            )
            
            if result["returncode"] != 0:
                raise GitError(f"Failed to list branches: {result['stderr']}")
            
            # Parse branch names
            branches = []
            for line in result["stdout"].splitlines():
                line = line.strip()
                if "->" not in line:  # Skip HEAD pointer
                    branch = line.split("/", 1)[1] if "/" in line else line
                    branches.append(branch)
            
            logger.info(f"Available branches: {', '.join(branches)}")
            return branches
            
        except Exception as e:
            self.cleanup()
            raise e
    
    def determine_main_branch(self, repo_path: str, available_branches: List[str]) -> str:
        """
        Determine the main/master branch if not specified.
        
        Args:
            repo_path: Path to the repository
            available_branches: List of available branches
            
        Returns:
            Main branch name
            
        Raises:
            GitError: If main branch cannot be determined
        """
        # If master branch is specified and exists, use it
        if self.master_branch and self.master_branch in available_branches:
            logger.info(f"Using specified master branch: {self.master_branch}")
            return self.master_branch
        
        # Try common default branch names
        for branch in DEFAULT_MAIN_BRANCHES:
            if branch in available_branches:
                logger.info(f"Found default main branch: {branch}")
                return branch
        
        # If couldn't determine, raise an error
        raise GitError(
            "Could not determine main branch. Available branches: "
            f"{', '.join(available_branches)}. Please specify with --master."
        )
    
    def checkout_branch(self, repo_path: str, branch: str) -> str:
        """
        Checkout a specific branch.
        
        Args:
            repo_path: Path to the repository
            branch: Branch name to checkout
            
        Returns:
            Path to the checked-out branch
            
        Raises:
            GitError: If checkout fails
        """
        branch_path = os.path.join(self.temp_dir, f"{self.repo_name}-{branch}")
        
        try:
            # Create a directory for the branch
            os.makedirs(branch_path, exist_ok=True)
            
            # Clone the repository to the branch directory
            result = self._run_git_command(
                ["git", "worktree", "add", branch_path, branch],
                cwd=repo_path
            )
            
            if result["returncode"] != 0:
                error_msg = result["stderr"].strip()
                
                if f"'{branch}' not found" in error_msg:
                    raise GitError(
                        f"Branch '{branch}' not found. "
                        f"Available branches: {', '.join(self.fetch_branches(repo_path))}"
                    )
                else:
                    raise GitError(f"Failed to checkout branch '{branch}': {error_msg}")
            
            logger.info(f"Successfully checked out branch '{branch}' to {branch_path}")
            return branch_path
            
        except Exception as e:
            # Clean up on error
            self.cleanup()
            raise e
    
    def compare_directories(self, dir1: str, dir2: str) -> List[Dict[str, str]]:
        """
        Compare two directories and return the differences.
        
        Args:
            dir1: Path to the first directory (base)
            dir2: Path to the second directory (comparison)
            
        Returns:
            List of dictionaries with file name and change type
        """
        changes = []
        
        # Get all files in both directories
        files1 = set()
        files2 = set()
        
        for root, _, files in os.walk(dir1):
            rel_root = os.path.relpath(root, dir1)
            for file in files:
                if rel_root == ".":
                    rel_path = file
                else:
                    rel_path = os.path.join(rel_root, file)
                files1.add(rel_path)
        
        for root, _, files in os.walk(dir2):
            rel_root = os.path.relpath(root, dir2)
            for file in files:
                if rel_root == ".":
                    rel_path = file
                else:
                    rel_path = os.path.join(rel_root, file)
                files2.add(rel_path)
        
        # Find added, deleted, and common files
        added_files = files2 - files1
        deleted_files = files1 - files2
        common_files = files1.intersection(files2)
        
        # Add added files to changes
        for file in added_files:
            changes.append({"filename": file, "changeType": "added"})
        
        # Add deleted files to changes
        for file in deleted_files:
            changes.append({"filename": file, "changeType": "deleted"})
        
        # Compare common files for modifications
        for file in common_files:
            file1 = os.path.join(dir1, file)
            file2 = os.path.join(dir2, file)
            
            if os.path.islink(file1) or os.path.islink(file2):
                # Handle symbolic links
                if os.path.islink(file1) and os.path.islink(file2):
                    link1 = os.readlink(file1)
                    link2 = os.readlink(file2)
                    if link1 != link2:
                        changes.append({"filename": file, "changeType": "modified"})
                    else:
                        changes.append({"filename": file, "changeType": "unchanged"})
                else:
                    changes.append({"filename": file, "changeType": "modified"})
            else:
                # Regular file comparison
                with open(file1, 'rb') as f1, open(file2, 'rb') as f2:
                    if f1.read() != f2.read():
                        changes.append({"filename": file, "changeType": "modified"})
                    else:
                        changes.append({"filename": file, "changeType": "unchanged"})
        
        return changes
    
    def cleanup(self):
        """Clean up temporary directories."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            logger.info(f"Cleaning up temporary directory: {self.temp_dir}")
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                logger.warning(f"Failed to clean up temporary directory: {e}")
    
    def run_comparison(self) -> List[Dict[str, str]]:
        """
        Run the full comparison process.
        
        Returns:
            List of dictionaries with file name and change type
        """
        try:
            # Clone repository
            repo_path = self.clone_repository()
            
            # Fetch available branches
            available_branches = self.fetch_branches(repo_path)
            
            # Check if feature branch exists
            if self.feature_branch not in available_branches:
                raise GitError(
                    f"Feature branch '{self.feature_branch}' not found. "
                    f"Available branches: {', '.join(available_branches)}"
                )
            
            # Determine main branch if not specified
            main_branch = self.determine_main_branch(repo_path, available_branches)
            
            # Checkout both branches
            main_path = self.checkout_branch(repo_path, main_branch)
            feature_path = self.checkout_branch(repo_path, self.feature_branch)
            
            # Compare directories
            logger.info(f"Comparing branches: {main_branch} and {self.feature_branch}")
            changes = self.compare_directories(main_path, feature_path)
            
            # Log summary of changes
            added = sum(1 for c in changes if c["changeType"] == "added")
            modified = sum(1 for c in changes if c["changeType"] == "modified")
            deleted = sum(1 for c in changes if c["changeType"] == "deleted")
            unchanged = sum(1 for c in changes if c["changeType"] == "unchanged")
            
            logger.info(
                f"Comparison complete - added: {added}, modified: {modified}, "
                f"deleted: {deleted}, unchanged: {unchanged}"
            )
            
            return changes
            
        except (GitError, GitTimeoutError) as e:
            logger.error(f"Error during comparison: {e}")
            raise
            
        finally:
            # Always clean up
            self.cleanup()


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Compare files between branches in a Git repository."
    )
    
    parser.add_argument(
        "--repo", 
        required=True,
        help="URL of the Git repository"
    )
    
    parser.add_argument(
        "--master",
        help="Name of the master/main branch (if not specified, will try to auto-detect)"
    )
    
    parser.add_argument(
        "--feature",
        required=True,
        help="Name of the feature branch to compare against master"
    )
    
    parser.add_argument(
        "--auth",
        help="Authentication in format 'username:token' for private repositories"
    )
    
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout in seconds for Git operations (default: {DEFAULT_TIMEOUT})"
    )
    
    parser.add_argument(
        "--output",
        help="Output file path for JSON results (default: print to stdout)"
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    
    return parser.parse_args()


def main():
    """Main function."""
    args = parse_arguments()
    
    # Set log level based on verbosity
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    # Set up signal handler for clean exit
    def signal_handler(sig, frame):
        logger.info("Received interrupt signal. Cleaning up...")
        sys.exit(1)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Create GitCompare object
        git_compare = GitCompare(
            repo_url=args.repo,
            master_branch=args.master,
            feature_branch=args.feature,
            auth=args.auth,
            timeout=args.timeout
        )
        
        # Run comparison
        changes = git_compare.run_comparison()
        
        # Output results
        if args.output:
            with open(args.output, 'w') as f:
                json.dump(changes, f, indent=2)
            logger.info(f"Results written to {args.output}")
        else:
            print(json.dumps(changes, indent=2))
        
    except (GitError, GitTimeoutError) as e:
        logger.error(str(e))
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Invalid argument: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
