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

  String _status = '正在准备审查环境';
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
    final startupNotice = await _waitForBackend();
    await minDelay;

    if (!mounted) {
      return;
    }

    setState(() {
      _status = '启动完成，正在进入工作台';
      _progress = 1;
    });

    await Future<void>.delayed(const Duration(milliseconds: 260));
    if (!mounted) {
      return;
    }

    Navigator.of(context).pushReplacement(
      PageRouteBuilder<void>(
        transitionDuration: const Duration(milliseconds: 420),
        pageBuilder: (_, __, ___) => ReviewPage(startupNotice: startupNotice),
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

  Future<String> _waitForBackend() async {
    final client = LocalApiClient(_defaultBaseUrl());
    const attempts = 14;

    Map<String, dynamic>? lastReport;
    ApiException? lastApiError;

    for (var i = 0; i < attempts; i++) {
      if (!mounted) {
        return '启动中断，请重新打开客户端。';
      }

      setState(() {
        _progress = (i + 1) / attempts;
        if (i < 4) {
          _status = '正在检查本地服务...';
        } else if (i < 10) {
          _status = '正在检查审查引擎...';
        } else {
          _status = '正在同步运行状态...';
        }
      });

      try {
        final report = await client.preflight();
        lastReport = report;
        if (report['ok'] == true) {
          if (mounted) {
            setState(() {
              _status = '启动检查通过，服务已就绪';
              _progress = 1;
            });
          }
          return '启动检查通过，服务已就绪。';
        }
      } on ApiException catch (e) {
        lastApiError = e;
      } catch (_) {
        // Keep retrying.
      }

      await Future<void>.delayed(const Duration(milliseconds: 520));
    }

    if (lastReport != null) {
      return _noticeFromPreflight(lastReport);
    }
    if (lastApiError != null) {
      return lastApiError.displayText;
    }
    return '系统仍在预热，你可以进入工作台后点击“启动检查”继续诊断。';
  }

  static String _noticeFromPreflight(Map<String, dynamic> report) {
    final code = (report['error_code'] ?? '').toString().trim();
    final message = (report['error_message'] ?? report['summary'] ?? '启动检查未通过').toString().trim();
    final suggestions = <String>[];
    final rawSuggestions = report['suggestions'];
    if (rawSuggestions is List) {
      for (final item in rawSuggestions) {
        final text = item.toString().trim();
        if (text.isNotEmpty) {
          suggestions.add(text);
        }
      }
    }

    final prefix = code.isEmpty ? '' : '[$code] ';
    if (suggestions.isEmpty) {
      return '$prefix$message';
    }
    return '$prefix$message\n建议：${suggestions.take(3).join('；')}';
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
                      '智能合同审查系统',
                      style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                            color: Colors.white,
                            fontWeight: FontWeight.w900,
                            letterSpacing: 0.3,
                          ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      '正在为你加载审查工作台',
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
