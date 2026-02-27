import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../../core/api/local_api_client.dart';
import '../review/review_page.dart';

class StartupGate extends StatefulWidget {
  const StartupGate({super.key});

  @override
  State<StartupGate> createState() => _StartupGateState();
}

class _StartupGateState extends State<StartupGate>
    with TickerProviderStateMixin {
  late final AnimationController _spinController;
  late final AnimationController _pulseController;

  String _status = '\u6b63\u5728\u51c6\u5907\u5ba1\u67e5\u73af\u5883';
  double _progress = 0.0;

  @override
  void initState() {
    super.initState();
    _spinController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 3),
    )..repeat();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);
    _bootstrap();
  }

  @override
  void dispose() {
    _spinController.dispose();
    _pulseController.dispose();
    super.dispose();
  }

  Future<void> _bootstrap() async {
    final minDelay = Future<void>.delayed(const Duration(milliseconds: 1400));
    final warmup = _waitForBackend();
    await Future.wait([minDelay, warmup]);

    if (!mounted) {
      return;
    }

    setState(() {
      _status = '\u542f\u52a8\u5b8c\u6210\uff0c\u6b63\u5728\u8fdb\u5165';
      _progress = 1;
    });

    await Future<void>.delayed(const Duration(milliseconds: 260));
    if (!mounted) {
      return;
    }

    Navigator.of(context).pushReplacement(
      PageRouteBuilder<void>(
        transitionDuration: const Duration(milliseconds: 420),
        pageBuilder: (_, __, ___) => const ReviewPage(),
        transitionsBuilder: (context, animation, secondary, child) {
          final slide = Tween<Offset>(
            begin: const Offset(0, 0.03),
            end: Offset.zero,
          ).animate(CurvedAnimation(parent: animation, curve: Curves.easeOutCubic));
          return FadeTransition(
            opacity: animation,
            child: SlideTransition(position: slide, child: child),
          );
        },
      ),
    );
  }

  Future<void> _waitForBackend() async {
    final client = LocalApiClient(_defaultBaseUrl());
    const attempts = 16;

    for (var i = 0; i < attempts; i++) {
      if (!mounted) {
        return;
      }

      setState(() {
        _progress = (i + 1) / attempts;
        _status = i < 5
            ? '\u6b63\u5728\u542f\u52a8\u672c\u5730\u670d\u52a1...'
            : i < 11
                ? '\u6b63\u5728\u8fde\u63a5\u5ba1\u67e5\u5f15\u64ce...'
                : '\u6b63\u5728\u540c\u6b65\u8fd0\u884c\u72b6\u6001...';
      });

      try {
        final data = await client.health();
        if (data['ok'] == true) {
          if (mounted) {
            setState(() {
              _status = '\u672c\u5730\u670d\u52a1\u5df2\u5c31\u7eea';
              _progress = 1;
            });
          }
          return;
        }
      } catch (_) {
        // ignore and retry
      }

      await Future<void>.delayed(const Duration(milliseconds: 520));
    }

    if (mounted) {
      setState(() {
        _status = '\u670d\u52a1\u4ecd\u5728\u9884\u70ed\uff0c\u5df2\u8fdb\u5165\u5de5\u4f5c\u53f0';
      });
    }
  }

  static String _defaultBaseUrl() {
    if (!kIsWeb && Platform.isAndroid) {
      return 'http://10.0.2.2:8003/contract';
    }
    return 'http://127.0.0.1:8003/contract';
  }

  @override
  Widget build(BuildContext context) {
    final pulse = CurvedAnimation(parent: _pulseController, curve: Curves.easeInOut);

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            colors: [Color(0xFF06213F), Color(0xFF0A7B72), Color(0xFF174E95)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
        ),
        child: Stack(
          children: [
            const Positioned(
              top: -90,
              left: -70,
              child: _GlowBlob(size: 250, color: Color(0x3300D9C0)),
            ),
            const Positioned(
              right: -80,
              bottom: -110,
              child: _GlowBlob(size: 290, color: Color(0x332B7BFF)),
            ),
            Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    ScaleTransition(
                      scale: Tween<double>(begin: 0.95, end: 1.04).animate(pulse),
                      child: RotationTransition(
                        turns: Tween<double>(begin: -0.015, end: 0.015).animate(pulse),
                        child: Container(
                          width: 118,
                          height: 118,
                          decoration: BoxDecoration(
                            color: Colors.white.withValues(alpha: 0.18),
                            shape: BoxShape.circle,
                            border: Border.all(
                              color: Colors.white.withValues(alpha: 0.35),
                              width: 1.2,
                            ),
                            boxShadow: const [
                              BoxShadow(
                                color: Color(0x30000000),
                                blurRadius: 28,
                                offset: Offset(0, 12),
                              ),
                            ],
                          ),
                          child: const Icon(
                            Icons.gavel_rounded,
                            color: Colors.white,
                            size: 58,
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 22),
                    Text(
                      '\u5408\u540c\u667a\u80fd\u5ba1\u67e5',
                      style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                            color: Colors.white,
                            fontWeight: FontWeight.w900,
                            letterSpacing: 0.3,
                          ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      '\u6b63\u5728\u4e3a\u4f60\u52a0\u8f7d\u5ba1\u67e5\u5de5\u4f5c\u53f0',
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                            color: const Color(0xFFD7E9FF),
                            fontWeight: FontWeight.w500,
                          ),
                    ),
                    const SizedBox(height: 20),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(999),
                      child: LinearProgressIndicator(
                        minHeight: 8,
                        value: _progress <= 0 ? null : _progress,
                        backgroundColor: Colors.white.withValues(alpha: 0.18),
                        valueColor: const AlwaysStoppedAnimation<Color>(Colors.white),
                      ),
                    ),
                    const SizedBox(height: 12),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        RotationTransition(
                          turns: _spinController,
                          child: const Icon(
                            Icons.autorenew_rounded,
                            size: 16,
                            color: Color(0xFFD9EAFF),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Text(
                          _status,
                          style: const TextStyle(
                            color: Color(0xFFD9EAFF),
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _GlowBlob extends StatelessWidget {
  const _GlowBlob({required this.size, required this.color});

  final double size;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: color,
          boxShadow: [
            BoxShadow(
              color: color,
              blurRadius: 80,
              spreadRadius: 8,
            ),
          ],
        ),
      ),
    );
  }
}
