package com.stephenshen.arkquant.repository;

import com.google.common.collect.Sets;
import com.stephenshen.arkquant.entity.Security;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.util.HashSet;
import java.util.List;
import java.util.Map;

/**
 * @author stephenshen
 * @date 2024/9/1 18:13:42
 */
@SpringBootTest
public class SecurityRepositoryTest {

    @Autowired
    private SecurityRepository securityRepository;

    @Test
    public void testSecurity() {
        Security security = securityRepository.getSecurity("128090");
        System.out.println(security);
    }

    @Test
    public void testSecurityMap() {
        HashSet<String> symbols = Sets.newHashSet("128090", "002820");
        Map<String, Security> securityMap = securityRepository.getSecurityMap(symbols);
        System.out.println(securityMap);
    }

    @Test
    public void testSecurities() {
        HashSet<String> symbols = Sets.newHashSet("128090", "002820");
        List<Security> securities = securityRepository.getSecurities(symbols);
        System.out.println(securities);
    }
}
