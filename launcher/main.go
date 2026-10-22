//go:build windows

// TravelClaim's launcher carries its offline Python and OCR runtime with it.
package main

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

//go:embed payload.zip
var payload []byte

var user32 = syscall.NewLazyDLL("user32.dll")
var messageBox = user32.NewProc("MessageBoxW")

func showError(err error) {
	message := "프로그램을 시작하지 못했습니다.\n\n" + err.Error()
	text, _ := syscall.UTF16PtrFromString(message)
	title, _ := syscall.UTF16PtrFromString("여비청구서 변환기")
	messageBox.Call(0, uintptr(unsafe.Pointer(text)), uintptr(unsafe.Pointer(title)), 0x10)
}

func runtimeRoot() (string, error) {
	base, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(payload)
	return filepath.Join(base, "TravelClaim", hex.EncodeToString(digest[:12])), nil
}

func complete(root string) bool {
	for _, name := range []string{".complete", "python/python.exe", "app/backend.py"} {
		info, err := os.Stat(filepath.Join(root, filepath.FromSlash(name)))
		if err != nil || !info.Mode().IsRegular() {
			return false
		}
	}
	return true
}

func unpack(root string) error {
	if complete(root) {
		return nil
	}
	parent := filepath.Dir(root)
	if err := os.MkdirAll(parent, 0700); err != nil {
		return err
	}
	staging, err := os.MkdirTemp(parent, "unpack-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(staging)
	reader, err := zip.NewReader(bytes.NewReader(payload), int64(len(payload)))
	if err != nil {
		return err
	}
	var total uint64
	for _, entry := range reader.File {
		name := strings.ReplaceAll(entry.Name, "\\", "/")
		rel := filepath.FromSlash(name)
		if !filepath.IsLocal(rel) || strings.Contains(name, ":") || entry.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("잘못된 내부 파일 경로: %q", name)
		}
		total += entry.UncompressedSize64
		if total > 700*1024*1024 {
			return fmt.Errorf("내부 파일 크기 제한을 초과했습니다")
		}
		target := filepath.Join(staging, rel)
		if entry.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0700); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
			return err
		}
		source, err := entry.Open()
		if err != nil {
			return err
		}
		dest, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			source.Close()
			return err
		}
		_, copyErr := io.Copy(dest, source)
		closeErr := dest.Close()
		source.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
	if err := os.WriteFile(filepath.Join(staging, ".complete"), []byte("ok\n"), 0600); err != nil {
		return err
	}
	if err := os.Rename(staging, root); err != nil {
		// Another concurrently launched instance may already have installed it.
		if complete(root) {
			return nil
		}
		return err
	}
	return nil
}

func run() error {
	root, err := runtimeRoot()
	if err != nil {
		return err
	}
	if err := unpack(root); err != nil {
		return fmt.Errorf("실행 파일 준비 실패: %w", err)
	}
	logPath := filepath.Join(filepath.Dir(root), "startup.log")
	log, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer log.Close()
	fmt.Fprintln(log, "\nTravelClaim launch", time.Now().Format(time.RFC3339))
	python := filepath.Join(root, "python", "python.exe")
	backend := filepath.Join(root, "app", "backend.py")
	command := exec.Command(python, "-X", "utf8", "-E", "-s", backend, "--desktop")
	command.Dir = filepath.Join(root, "app")
	command.Stdout = log
	command.Stderr = log
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if err := command.Run(); err != nil {
		return fmt.Errorf("변환기 실행 실패: %w\n실행 기록: %s", err, logPath)
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		showError(err)
	}
}
