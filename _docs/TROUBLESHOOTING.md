# Keeprollming Orchestrator Troubleshooting Guide

This guide provides solutions for common issues that users may encounter when setting up, configuring, or using the Keeprollming orchestrator.

## Overview

The Keeprollming orchestrator is designed to be robust and handle various edge cases gracefully. However, some common configuration issues can arise during setup or usage that require specific troubleshooting approaches. This guide covers typical scenarios and their solutions.

## Common Issues and Solutions

### 1. Connection Timeout Errors
**Problem**: API requests fail with timeout errors when communicating with upstream models.
**Solution**: 
- Check the `UPSTREAM_BASE_URL` environment variable is correctly configured
- Ensure the upstream server (e.g., LM Studio) is running and accessible  
- Increase timeout values if using slow models or network conditions

### 2. Context Overflow Handling Issues  
**Problem**: Conversation history exceeds context limits but summarization fails.
**Solution**:
- Verify that `SUMMARY_CACHE_ENABLED` is set to true for better performance
- Check that the summary model (`SUMMARY_MODEL`) has sufficient capacity  
- Adjust `MAX_HEAD` and `MAX_TAIL` values based on your specific use case

### 3. Model Configuration Mismatches
**Problem**: Using profile names that don't match configured models.
**Solution**:
- Review configuration in `config.yaml` to ensure all profiles are defined correctly
- Verify that model aliases referenced in API requests exist in the configuration  
- Use valid profile combinations like `"local/main"` or `"pass/gpt-4"`

### 4. Streaming Response Format Errors
**Problem**: Client receives malformed SSE responses.
**Solution**:
- Ensure `stream` parameter is properly set to boolean value in request 
- Check that client application can handle Server-Sent Events correctly  
- Review API documentation for proper streaming response format

### 5. Performance Bottlenecks  
**Problem**: Slow response times with large conversation histories.
**Solution**:
- Enable connection pooling by setting appropriate upstream settings
- Optimize memory usage by reviewing context management parameters
- Consider using caching strategies to reduce redundant processing  

## Error Code Reference

### HTTP Status Codes and Their Meanings:

| Status Code | Description |
|-------------|-------------|
| 400         | Invalid request payload format |
| 429         | Rate limiting or resource exhaustion |
| 500         | Internal server error during processing |

### Common Response Error Messages:
- `"Invalid model name"` - Model configuration not found
- `"Context overflow detected"` - Conversation history exceeds limits  
- `"Connection timeout"` - Upstream server unreachable

## Configuration Validation Tips

1. **Environment Variables**: Always check that required environment variables are set before starting the application.
2. **Model Availability**: Verify that all referenced models are available in your upstream backend.
3. **Network Connectivity**: Ensure there's stable network connectivity between orchestrator and upstream servers.

## Debugging Tools

The orchestrator includes comprehensive logging capabilities:
- Enable debug mode with `DEBUG=true` environment variable  
- Review application logs for detailed error information
- Use the built-in debugging scripts to trace request processing

## Contact Support

For issues not covered in this guide, please consult the project documentation or reach out through the project's support channels.